"""`Publication` gravada no caminho de sucesso de `POST /schedule`.

Reusa o mesmo padrão de mock de `test_schedule.py`/`test_youtube.py`
(`BufferClient` e `generate_presigned_url` via `unittest.mock.patch`, nenhuma
chamada de rede de verdade) e lê de volta pelo fixture `session` de
`conftest.py`. Requer Postgres em `DATABASE_URL` — ver `tests/conftest.py`.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from sqlalchemy import select

from src.core import settings
from src.tiktok_poster.buffer.client import BufferRejected
from src.tiktok_poster.buffer.scheduler import continuation_slot, timing_bucket
from src.tiktok_poster.db.models import Publication

from tests.conftest import BUFFER_CREATE_RESPONSE, SAMPLE_REQUEST

YOUTUBE_CREATE_RESPONSE = {"updates": [{"id": "yt_update_001"}]}

#: `preferred_times` do `config.ini` de teste — 14:00/18:00/22:00 UTC, que são
#: 11h/15h/19h em Brasília. Mesmos valores que `test_posting_window.py` já
#: verifica contra o arquivo real.
PREFERRED_TIMES = ["14:00", "18:00", "22:00"]


@pytest.fixture
def youtube_on(monkeypatch):
    """Liga o destino do YouTube — mesmo fixture de `test_youtube.py`, redefinido
    aqui para não depender de importar um módulo de teste de outro."""
    monkeypatch.setattr(
        "src.tiktok_poster.api.routes.schedule._youtube_channel_id",
        lambda: "yt_channel_id",
    )
    monkeypatch.setattr(settings.CONFIG.youtube, "enabled", True)


async def _publications(session, series_id: str) -> list[Publication]:
    result = await session.execute(
        select(Publication).where(Publication.series_id == UUID(series_id))
    )
    return list(result.scalars().all())


# ── gravação no agendamento ────────────────────────────────────────────────

async def test_publication_written_for_tiktok_destination(client, session):
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    rows = await _publications(session, SAMPLE_REQUEST["series_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row.channel == "tiktok"
    assert row.buffer_post_id == "buf_update_001"
    assert row.part_number == SAMPLE_REQUEST["part_number"]
    assert row.series_id == UUID(SAMPLE_REQUEST["series_id"])
    expected_seed = f"tiktok:{SAMPLE_REQUEST['series_id']}:{SAMPLE_REQUEST['part_number']}"
    assert row.hashtag_variant == expected_seed
    assert row.template_id is None
    assert row.tts_voice is None


async def test_publication_written_for_youtube_destination(client, session, youtube_on):
    create_mock = AsyncMock(side_effect=[BUFFER_CREATE_RESPONSE, YOUTUBE_CREATE_RESPONSE])
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        resp = await client.post(
            "/schedule", json={**SAMPLE_REQUEST, "youtube_title": "O e-mail do meu chefe"}
        )

    assert resp.status_code == 201
    rows = await _publications(session, SAMPLE_REQUEST["series_id"])
    by_channel = {r.channel: r for r in rows}
    assert set(by_channel) == {"tiktok", "youtube"}

    yt_row = by_channel["youtube"]
    assert yt_row.buffer_post_id == "yt_update_001"
    expected_seed = f"youtube:{SAMPLE_REQUEST['series_id']}:{SAMPLE_REQUEST['part_number']}"
    assert yt_row.hashtag_variant == expected_seed
    # O mesmo slot vale para os dois destinos — o rótulo de horário também.
    assert yt_row.timing_bucket == by_channel["tiktok"].timing_bucket


async def test_publication_carries_template_id_and_tts_voice(client, session):
    template_id = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
        resp = await client.post(
            "/schedule",
            json={**SAMPLE_REQUEST, "template_id": template_id, "tts_voice": "pt-BR-AntonioNeural"},
        )

    assert resp.status_code == 201
    rows = await _publications(session, SAMPLE_REQUEST["series_id"])
    assert rows[0].template_id == UUID(template_id)
    assert rows[0].tts_voice == "pt-BR-AntonioNeural"


async def test_no_publication_written_when_buffer_rejects(client, session):
    """`BufferRejected` sem marcador de teto sobe como está (ver
    `test_queue_backpressure.py::test_real_rejection_still_fails`) — o cliente
    de teste propaga a exceção em vez de virar 500, então é isto que se espera.
    """
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = AsyncMock(side_effect=BufferRejected("conteúdo recusado"))
        with pytest.raises(BufferRejected):
            await client.post("/schedule", json=SAMPLE_REQUEST)

    rows = await _publications(session, SAMPLE_REQUEST["series_id"])
    assert rows == []


async def test_no_publication_written_when_queue_is_full(client, session):
    full_queue = [{"scheduled_at": 1700000000 + i * 3600} for i in range(10)]
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=full_queue)
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 429
    rows = await _publications(session, SAMPLE_REQUEST["series_id"])
    assert rows == []


async def test_no_youtube_publication_when_youtube_fails(client, session, youtube_on):
    """O TikTok já está gravado quando o YouTube falha — só a linha dele existe."""
    create_mock = AsyncMock(side_effect=[BUFFER_CREATE_RESPONSE, RuntimeError("Buffer recusou")])
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        resp = await client.post("/schedule", json={**SAMPLE_REQUEST, "youtube_title": "Título"})

    assert resp.status_code == 201
    rows = await _publications(session, SAMPLE_REQUEST["series_id"])
    assert [r.channel for r in rows] == ["tiktok"]


# ── timing_bucket (pura) ────────────────────────────────────────────────────

def test_timing_bucket_labels_each_preferred_slot():
    assert timing_bucket(datetime(2026, 9, 6, 14, 0, tzinfo=timezone.utc), PREFERRED_TIMES) == "11h"
    assert timing_bucket(datetime(2026, 9, 6, 18, 0, tzinfo=timezone.utc), PREFERRED_TIMES) == "15h"
    assert timing_bucket(datetime(2026, 9, 6, 22, 0, tzinfo=timezone.utc), PREFERRED_TIMES) == "19h"


def test_timing_bucket_is_other_for_a_continuation_slot():
    """Continuação pendura `series_gap_minutes` (30) depois do slot anterior —
    mais longe que a tolerância de `timing_bucket`, então não deve colar no
    mesmo rótulo do slot que a originou."""
    previous_slot = datetime(2026, 9, 6, 14, 0, tzinfo=timezone.utc)  # 11h BRT
    slot = continuation_slot(previous_slot, gap_minutes=30, pending_posts=[], queue_limit=10,
                              now=datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc))
    assert timing_bucket(slot, PREFERRED_TIMES) == "other"


def test_timing_bucket_is_other_far_from_any_preferred_slot():
    assert timing_bucket(datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc), PREFERRED_TIMES) == "other"


def test_timing_bucket_accepts_naive_datetime_as_utc():
    """`scheduled_at` sem tzinfo é tratado como UTC — mesma convenção de
    `continuation_slot`."""
    assert timing_bucket(datetime(2026, 9, 6, 14, 0), PREFERRED_TIMES) == "11h"
