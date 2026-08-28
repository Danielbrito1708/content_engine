"""Publicação do mesmo vídeo no YouTube, pelo mesmo Buffer.

O YouTube é um **segundo destino**, não um segundo pipeline: o vídeo, o slot e a
decisão de ritmo são os mesmos do TikTok. O que muda é o que a API exige — lá o
post tem título e categoria obrigatórios — e o que acontece quando dá errado:
o TikTok é o destino principal e sua falha derruba o request, a do YouTube não.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.core import settings
from src.tiktok_poster.youtube.metadata import (
    DEFAULT_CATEGORY_ID,
    MAX_TITLE_CHARS,
    build_metadata,
    compose_title,
    youtube_category_id,
)

from tests.conftest import BUFFER_CREATE_RESPONSE, SAMPLE_REQUEST

YOUTUBE_CREATE_RESPONSE = {"updates": [{"id": "yt_update_001"}]}


@pytest.fixture
def youtube_on(monkeypatch):
    """Liga o destino do YouTube: canal configurado e seção habilitada."""
    monkeypatch.setattr(
        "src.tiktok_poster.api.routes.schedule._youtube_channel_id",
        lambda: "yt_channel_id",
    )
    monkeypatch.setattr(settings.CONFIG.youtube, "enabled", True)


# ── youtube_category_id ────────────────────────────────────────────────────

def test_category_maps_known_content_types():
    assert youtube_category_id("comédia") == "23"
    assert youtube_category_id("educativo") == "27"
    assert youtube_category_id("entretenimento") == "24"


def test_category_is_case_and_accent_tolerant():
    assert youtube_category_id("  COMEDIA ") == "23"


def test_category_falls_back_for_unmapped_types():
    """`drama` e `suspense` não têm categoria própria no YouTube."""
    assert youtube_category_id("drama") == DEFAULT_CATEGORY_ID
    assert youtube_category_id("suspense") == DEFAULT_CATEGORY_ID
    assert youtube_category_id(None) == DEFAULT_CATEGORY_ID
    assert youtube_category_id("inventado", default="27") == "27"


# ── compose_title ──────────────────────────────────────────────────────────

def test_title_has_no_label_for_a_single_part_story():
    assert compose_title("O e-mail do meu chefe", 1, 1) == "O e-mail do meu chefe"


def test_title_carries_the_part_label_in_a_series():
    assert compose_title("O e-mail do meu chefe", 2, 3) == "O e-mail do meu chefe (Parte 2/3)"


def test_title_collapses_whitespace():
    assert compose_title("  dois   espaços ", 1, 1) == "dois espaços"


def test_title_shrinks_to_protect_the_part_label():
    """Numa série, o rótulo é o que não pode faltar — quem encolhe é o título."""
    result = compose_title("palavra " * 30, 2, 2)
    assert len(result) <= MAX_TITLE_CHARS
    assert result.endswith("(Parte 2/2)")


def test_title_respects_the_ceiling_without_a_label():
    result = compose_title("palavra " * 30, 1, 1)
    assert len(result) <= MAX_TITLE_CHARS
    assert result.endswith("palavra")


# ── build_metadata ─────────────────────────────────────────────────────────

def test_metadata_nests_under_the_youtube_key():
    meta = build_metadata("Título", "22")
    assert set(meta) == {"youtube"}
    assert meta["youtube"]["title"] == "Título"


def test_metadata_stringifies_the_category():
    """O ini devolve `category_id` como int; o schema do Buffer pede String."""
    assert build_metadata("t", 22)["youtube"]["categoryId"] == "22"


def test_metadata_discloses_ai_by_default():
    """A narração é voz sintética e o YouTube pede a declaração."""
    assert build_metadata("t", "22")["youtube"]["isAiGenerated"] is True


def test_metadata_defaults_to_public_and_not_for_kids():
    meta = build_metadata("t", "22")["youtube"]
    assert meta["privacy"] == "public"
    assert meta["madeForKids"] is False


# ── rota: os dois destinos ─────────────────────────────────────────────────

async def test_schedule_posts_to_both_channels(client, youtube_on):
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
    data = resp.json()
    assert data["buffer_update_id"] == "buf_update_001"
    assert data["youtube_update_id"] == "yt_update_001"
    assert data["youtube_error"] is None
    assert create_mock.await_count == 2
    assert mock_buf.call_args.kwargs["channel_id"] == "yt_channel_id"


async def test_both_channels_get_the_same_slot(client, youtube_on):
    """Uma decisão de ritmo só: a história sai nos dois lugares no mesmo horário."""
    create_mock = AsyncMock(side_effect=[BUFFER_CREATE_RESPONSE, YOUTUBE_CREATE_RESPONSE])
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        await client.post("/schedule", json={**SAMPLE_REQUEST, "youtube_title": "Título"})

    tiktok_slot = create_mock.await_args_list[0][0][2]
    youtube_slot = create_mock.await_args_list[1][0][2]
    assert tiktok_slot == youtube_slot


async def test_youtube_post_carries_title_and_metadata(client, youtube_on):
    create_mock = AsyncMock(side_effect=[BUFFER_CREATE_RESPONSE, YOUTUBE_CREATE_RESPONSE])
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        await client.post(
            "/schedule",
            json={
                **SAMPLE_REQUEST,
                "part_number": 2,
                "total_parts": 2,
                "youtube_title": "O e-mail do meu chefe",
            },
        )

    metadata = create_mock.await_args_list[1].kwargs["metadata"]["youtube"]
    assert metadata["title"] == "O e-mail do meu chefe (Parte 2/2)"
    # SAMPLE_CLASSIFICATION é "drama", que não tem categoria própria no YouTube.
    assert metadata["categoryId"] == DEFAULT_CATEGORY_ID


async def test_tiktok_post_never_gets_youtube_metadata(client, youtube_on):
    """O caminho do TikTok publica em produção hoje — ele não pode mudar."""
    create_mock = AsyncMock(side_effect=[BUFFER_CREATE_RESPONSE, YOUTUBE_CREATE_RESPONSE])
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        await client.post("/schedule", json={**SAMPLE_REQUEST, "youtube_title": "Título"})

    assert "metadata" not in create_mock.await_args_list[0].kwargs


async def test_youtube_description_uses_its_own_mandatory_hashtags(client, youtube_on):
    """Hashtag de YouTube é busca, não distribuição — as obrigatórias de lá são
    outras, e é isto que impede o teste de medir o post errado sem ninguém notar.
    O TikTok não tem mais obrigatórias (ver `test_schedule.py`), então a
    separação é medida pelas do YouTube não vazarem para a legenda de lá."""
    create_mock = AsyncMock(side_effect=[BUFFER_CREATE_RESPONSE, YOUTUBE_CREATE_RESPONSE])
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        await client.post("/schedule", json={**SAMPLE_REQUEST, "youtube_title": "Título"})

    tiktok_caption = create_mock.await_args_list[0][0][1]
    youtube_description = create_mock.await_args_list[1][0][1]
    assert "#historiasreais" in youtube_description
    assert "#historiasreais" not in tiktok_caption
    assert "#reddit" not in tiktok_caption


# ── rota: degradação ───────────────────────────────────────────────────────

async def test_youtube_is_skipped_when_no_channel_is_configured(client, youtube_on):
    """Sem canal conectado o serviço segue postando só no TikTok, sem erro.

    Precisa do `youtube_on` para chegar até a checagem de canal: com o destino
    desligado no `config.ini` (desde 27/08/2026) o `enabled` curto-circuita
    antes, e o teste mediria a mensagem do freio manual, não a do canal ausente."""
    create_mock = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule._youtube_channel_id", return_value=""),
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    assert resp.json()["youtube_update_id"] is None
    assert "não configurado" in resp.json()["youtube_error"]
    assert create_mock.await_count == 1


async def test_youtube_can_be_turned_off_by_config(client, youtube_on, monkeypatch):
    monkeypatch.setattr(settings.CONFIG.youtube, "enabled", False)
    create_mock = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    assert "desligado" in resp.json()["youtube_error"]
    assert create_mock.await_count == 1


async def test_youtube_failure_does_not_fail_the_request(client, youtube_on):
    """O TikTok já está agendado quando o YouTube roda: derrubar aqui faria o
    retry do orchestrador republicar a parte no TikTok."""
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
    data = resp.json()
    assert data["buffer_update_id"] == "buf_update_001"
    assert data["youtube_update_id"] is None
    assert "Buffer recusou" in data["youtube_error"]


async def test_youtube_reports_a_missing_post_id(client, youtube_on):
    create_mock = AsyncMock(side_effect=[BUFFER_CREATE_RESPONSE, {"updates": [{}]}])
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        resp = await client.post("/schedule", json={**SAMPLE_REQUEST, "youtube_title": "Título"})

    assert resp.json()["youtube_update_id"] is None
    assert "id" in resp.json()["youtube_error"]


async def test_youtube_falls_back_to_the_cta_when_no_title_is_sent(client, youtube_on):
    """Orchestrador antigo não manda `youtube_title` — e sem título o Buffer recusa."""
    create_mock = AsyncMock(side_effect=[BUFFER_CREATE_RESPONSE, YOUTUBE_CREATE_RESPONSE])
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        await client.post("/schedule", json=SAMPLE_REQUEST)

    assert create_mock.await_args_list[1].kwargs["metadata"]["youtube"]["title"].startswith(
        "Comenta o que você faria"
    )


async def test_youtube_is_skipped_when_there_is_no_title_at_all(client, youtube_on):
    create_mock = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
    no_cta = {**SAMPLE_REQUEST, "classification": {"hashtag_hints": [], "cta_per_part": []}}
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        resp = await client.post("/schedule", json=no_cta)

    assert resp.status_code == 201
    assert "sem título" in resp.json()["youtube_error"]
    assert create_mock.await_count == 1


async def test_queue_full_still_returns_429_before_touching_youtube(client, youtube_on):
    """O freio da fila é do TikTok e continua parando o run inteiro."""
    full_queue = [{"scheduled_at": 1700000000 + i * 3600} for i in range(10)]
    create_mock = AsyncMock()
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=full_queue)
        mock_buf.return_value.create_post = create_mock
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 429
    assert create_mock.await_count == 0


# ── BufferClient: a mutation ───────────────────────────────────────────────

async def test_client_omits_the_metadata_clause_when_there_is_none():
    """Mandar `metadata: null` mudaria a mutation que publica no TikTok hoje —
    servidor GraphQL não é obrigado a tratar null explícito como ausente."""
    from datetime import datetime, timezone

    from src.tiktok_poster.buffer.client import BufferClient

    client = BufferClient()
    with patch.object(
        BufferClient, "_graphql", new_callable=AsyncMock,
        return_value={"data": {"createPost": {"__typename": "PostActionSuccess",
                                              "post": {"id": "p1"}}}},
    ) as gql:
        await client.create_post("https://v/1.mp4", "legenda", datetime.now(tz=timezone.utc))

    query, variables = gql.await_args[0]
    assert "$metadata" not in query
    assert "metadata:" not in query
    assert "metadata" not in variables


async def test_client_declares_the_metadata_variable_when_given_one():
    from datetime import datetime, timezone

    from src.tiktok_poster.buffer.client import BufferClient

    client = BufferClient(channel_id="yt_channel_id")
    meta = build_metadata("Título", "22")
    with patch.object(
        BufferClient, "_graphql", new_callable=AsyncMock,
        return_value={"data": {"createPost": {"__typename": "PostActionSuccess",
                                              "post": {"id": "p2"}}}},
    ) as gql:
        await client.create_post(
            "https://v/1.mp4", "descrição", datetime.now(tz=timezone.utc), metadata=meta
        )

    query, variables = gql.await_args[0]
    assert "$metadata: PostInputMetaData" in query
    assert "metadata: $metadata," in query
    assert variables["metadata"] == meta
    assert variables["channelId"] == "yt_channel_id"


def test_client_defaults_to_the_tiktok_channel():
    from src.tiktok_poster.buffer.client import BufferClient

    assert BufferClient()._channel_id == settings.env.buffer_profile_id
    assert BufferClient(channel_id="outro")._channel_id == "outro"


# ── youtube_enabled: desligado não é degradação ────────────────────────────

async def test_enabled_is_false_when_the_channel_is_missing(client):
    """Destino desligado é configuração, não falha — o orchestrador não avisa."""
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule._youtube_channel_id", return_value=""),
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.json()["youtube_enabled"] is False


async def test_enabled_is_true_when_the_destination_is_on_and_fails(client, youtube_on):
    """Ligado e sem publicar é o caso que precisa virar aviso."""
    create_mock = AsyncMock(side_effect=[BUFFER_CREATE_RESPONSE, RuntimeError("boom")])
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = create_mock
        resp = await client.post("/schedule", json={**SAMPLE_REQUEST, "youtube_title": "T"})

    assert resp.json()["youtube_enabled"] is True
    assert resp.json()["youtube_update_id"] is None
