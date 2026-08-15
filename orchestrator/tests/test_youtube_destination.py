"""O YouTube como segundo destino do mesmo vídeo.

O orchestrador não conhece o YouTube: ele manda o título que o refino escreveu e
lê de volta o que o poster conseguiu fazer. O que é dele é a persistência (para
a ausência do post ser auditável depois) e o aviso de degradação — porque o run
termina `scheduled` publicando nos dois lugares ou em um só, e o status não
distingue os dois casos.
"""

import json
from unittest.mock import patch

import respx
from httpx import Response

from src.orchestrator.clients.llm import LLMClient
from src.orchestrator.db.models import PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.worker import _refine, _schedule

SCHEDULE_URL = "http://tiktok_poster:8000/schedule"
REFINE_URL = "http://llm_service:8000/refine"


async def _make_run(session, **kwargs):
    kwargs.setdefault("status", PipelineStatus.processing)
    run = PipelineRun(raw_script="Roteiro.", classification={}, **kwargs)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _make_part(session, run_id, number=1):
    part = PipelinePart(
        run_id=run_id, part_number=number, script=f"Parte {number}.", video_key="videos/x.mp4"
    )
    session.add(part)
    await session.commit()
    await session.refresh(part)
    return part


def _poster_response(**overrides):
    body = {
        "scheduled_at": "2026-08-20T12:00:00+00:00",
        "buffer_update_id": "buf_1",
        "youtube_update_id": "yt_1",
        "youtube_error": None,
        "youtube_enabled": True,
    }
    body.update(overrides)
    return Response(200, json=body)


def _sent(route):
    return [json.loads(call.request.content) for call in route.calls]


# ── o título vem do refino ─────────────────────────────────────────────────

@respx.mock
async def test_refine_persists_the_youtube_title(session):
    respx.post(REFINE_URL).mock(
        return_value=Response(
            200,
            json={
                "parts": ["Texto."],
                "classification": {"content_type": "drama"},
                "hook": "O gancho.",
                "narrator_gender": "female",
                "youtube_title": "O e-mail que meu chefe não devia ter mandado",
            },
        )
    )
    run = await _make_run(session, status=PipelineStatus.pending)

    await _refine(session, run)

    assert run.youtube_title == "O e-mail que meu chefe não devia ter mandado"


@respx.mock
async def test_refine_survives_an_llm_service_without_the_field(session):
    """Deploy dos dois serviços não é atômico — campo ausente não é erro."""
    respx.post(REFINE_URL).mock(
        return_value=Response(
            200,
            json={"parts": ["Texto."], "classification": {}, "hook": "O gancho."},
        )
    )
    run = await _make_run(session, status=PipelineStatus.pending)

    await _refine(session, run)

    assert run.youtube_title is None


@respx.mock
async def test_llm_client_reads_the_title():
    respx.post(REFINE_URL).mock(
        return_value=Response(
            200,
            json={"parts": ["t"], "classification": {}, "youtube_title": "  Título  "},
        )
    )
    result = await LLMClient().refine("roteiro", {})
    assert result.youtube_title == "Título"


# ── o título chega ao poster ───────────────────────────────────────────────

@respx.mock
async def test_schedule_sends_the_title_to_the_poster(session):
    route = respx.post(SCHEDULE_URL).mock(return_value=_poster_response())
    run = await _make_run(session, youtube_title="O e-mail do meu chefe")
    await _make_part(session, run.id)

    await _schedule(session, run)

    assert _sent(route)[0]["youtube_title"] == "O e-mail do meu chefe"


@respx.mock
async def test_a_run_without_a_title_omits_the_field(session):
    """Omitido, não `null` — mesmo contrato do `narrator_gender` no TTS."""
    route = respx.post(SCHEDULE_URL).mock(return_value=_poster_response())
    run = await _make_run(session)
    await _make_part(session, run.id)

    await _schedule(session, run)

    assert "youtube_title" not in _sent(route)[0]


@respx.mock
async def test_every_part_of_a_series_carries_the_same_title(session):
    """O título é do run: o que distingue as partes é o rótulo, que o poster põe."""
    route = respx.post(SCHEDULE_URL).mock(
        side_effect=[
            _poster_response(scheduled_at="2026-08-20T12:00:00+00:00", buffer_update_id="b1"),
            _poster_response(scheduled_at="2026-08-20T12:30:00+00:00", buffer_update_id="b2"),
        ]
    )
    run = await _make_run(session, youtube_title="Um título só")
    await _make_part(session, run.id, 1)
    await _make_part(session, run.id, 2)

    await _schedule(session, run)

    titles = [payload["youtube_title"] for payload in _sent(route)]
    assert titles == ["Um título só", "Um título só"]


# ── o ID volta e é persistido ──────────────────────────────────────────────

@respx.mock
async def test_schedule_persists_the_youtube_post_id(session):
    respx.post(SCHEDULE_URL).mock(return_value=_poster_response(youtube_update_id="yt_abc"))
    run = await _make_run(session, youtube_title="T")
    part = await _make_part(session, run.id)

    await _schedule(session, run)
    await session.refresh(part)

    assert part.tiktok_video_id == "buf_1"
    assert part.youtube_video_id == "yt_abc"


@respx.mock
async def test_a_poster_without_youtube_fields_leaves_the_column_null(session):
    """Poster antigo devolve só os dois campos de sempre."""
    respx.post(SCHEDULE_URL).mock(
        return_value=Response(
            200, json={"scheduled_at": "2026-08-20T12:00:00+00:00", "buffer_update_id": "buf_1"}
        )
    )
    run = await _make_run(session)
    part = await _make_part(session, run.id)

    await _schedule(session, run)
    await session.refresh(part)

    assert part.youtube_video_id is None
    assert run.status == PipelineStatus.scheduled


# ── a degradação vira aviso, e só quando é degradação ──────────────────────

@respx.mock
async def test_a_failed_youtube_post_does_not_fail_the_run(session):
    respx.post(SCHEDULE_URL).mock(
        return_value=_poster_response(youtube_update_id=None, youtube_error="Buffer recusou")
    )
    run = await _make_run(session, youtube_title="T")
    part = await _make_part(session, run.id)

    assert await _schedule(session, run) is True
    await session.refresh(part)
    assert run.status == PipelineStatus.scheduled
    assert part.scheduled_at is not None
    assert part.youtube_video_id is None


@respx.mock
async def test_a_failed_youtube_post_is_notified_as_a_degradation(session):
    respx.post(SCHEDULE_URL).mock(
        return_value=_poster_response(youtube_update_id=None, youtube_error="Buffer recusou")
    )
    run = await _make_run(session, youtube_title="T")
    await _make_part(session, run.id)

    sent: list[tuple] = []
    with patch("src.orchestrator.worker.notify", side_effect=lambda t, **k: sent.append((t, k))):
        await _schedule(session, run)

    warnings = [t for t, kw in sent if kw.get("level") == "warning"]
    assert any("YouTube" in text for text in warnings)


@respx.mock
async def test_a_disabled_destination_is_not_a_degradation(session):
    """Canal não conectado é configuração. Um alarme que toca sempre ninguém lê."""
    respx.post(SCHEDULE_URL).mock(
        return_value=_poster_response(
            youtube_update_id=None,
            youtube_error="canal do youtube não configurado",
            youtube_enabled=False,
        )
    )
    run = await _make_run(session)
    await _make_part(session, run.id)

    sent: list[tuple] = []
    with patch("src.orchestrator.worker.notify", side_effect=lambda t, **k: sent.append((t, k))):
        await _schedule(session, run)

    warnings = [t for t, kw in sent if kw.get("level") == "warning"]
    assert warnings == []


@respx.mock
async def test_the_api_exposes_both_ids(session, client):
    respx.post(SCHEDULE_URL).mock(return_value=_poster_response(youtube_update_id="yt_abc"))
    run = await _make_run(session, youtube_title="Um título")
    await _make_part(session, run.id)
    await _schedule(session, run)

    resp = await client.get(f"/pipeline/{run.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["youtube_title"] == "Um título"
    assert body["parts"][0]["youtube_video_id"] == "yt_abc"
