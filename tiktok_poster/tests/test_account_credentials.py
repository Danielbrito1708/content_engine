"""Fase 1 do multi-account: credenciais isoladas por conta (docs/multi_account.md).

`account_id` ausente tem que se comportar exatamente como antes desta feature
existir — é o requisito de retrocompatibilidade que os testes sem DB protegem.
Os que tocam o banco (`account_credentials`) exigem `DATABASE_URL` apontando
para um Postgres de verdade, mesma convenção do orchestrator/content_scout.
"""

import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.tiktok_poster.accounts.crypto import CredentialsKeyMissing, decrypt_token, encrypt_token
from tests.conftest import BUFFER_CREATE_RESPONSE, SAMPLE_REQUEST
from tests.test_youtube import youtube_on  # noqa: F401 — fixture reuso


# ── crypto puro, sem banco ───────────────────────────────────────────────


def test_encrypt_decrypt_round_trip():
    enc = encrypt_token("buffer-secret-token")
    assert enc != "buffer-secret-token"
    assert decrypt_token(enc) == "buffer-secret-token"


def test_encrypt_without_key_raises(monkeypatch):
    monkeypatch.setattr("src.tiktok_poster.accounts.crypto._credentials_key", lambda: None)
    with pytest.raises(CredentialsKeyMissing):
        encrypt_token("x")


# ── API /accounts — cadastro de credenciais ─────────────────────────────


async def test_create_account_credentials_never_returns_token(client):
    account_id = str(uuid.uuid4())
    resp = await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_teste",
            "buffer_access_token": "super-secret",
            "tiktok_channel_id": "chan_tiktok_1",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["account_id"] == account_id
    assert data["slug"] == "conta_teste"
    assert "buffer_access_token" not in data
    assert "buffer_token_enc" not in data
    assert "super-secret" not in resp.text


async def test_list_account_credentials_includes_created(client):
    account_id = str(uuid.uuid4())
    await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_lista",
            "buffer_access_token": "tok",
            "tiktok_channel_id": "chan_x",
        },
    )
    resp = await client.get("/accounts")
    assert resp.status_code == 200
    assert any(a["account_id"] == account_id for a in resp.json())


async def test_create_account_credentials_upserts_by_account_id(client):
    account_id = str(uuid.uuid4())
    await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_v1",
            "buffer_access_token": "tok-v1",
            "tiktok_channel_id": "chan_v1",
        },
    )
    resp = await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_v2",
            "buffer_access_token": "tok-v2",
            "tiktok_channel_id": "chan_v2",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["slug"] == "conta_v2"

    listed = (await client.get("/accounts")).json()
    matches = [a for a in listed if a["account_id"] == account_id]
    assert len(matches) == 1, "upsert não pode duplicar a linha da conta"
    assert matches[0]["tiktok_channel_id"] == "chan_v2"


# ── POST /schedule usando a conta certa ─────────────────────────────────


async def test_schedule_with_known_account_uses_its_credentials(client):
    account_id = str(uuid.uuid4())
    await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_dois",
            "buffer_access_token": "token-da-conta-dois",
            "tiktok_channel_id": "chan_conta_dois",
        },
    )

    with patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf:
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)

        resp = await client.post("/schedule", json={**SAMPLE_REQUEST, "account_id": account_id})

    assert resp.status_code == 201
    # Uma única instanciação: a conta não tem youtube_channel_id, então
    # `_schedule_youtube` nunca chega a criar um segundo `BufferClient`.
    assert mock_buf.call_count == 1
    _, kwargs = mock_buf.call_args
    assert kwargs["access_token"] == "token-da-conta-dois"
    assert kwargs["channel_id"] == "chan_conta_dois"


async def test_schedule_with_unknown_account_falls_back_to_default(client):
    with patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf:
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)

        resp = await client.post("/schedule", json={**SAMPLE_REQUEST, "account_id": str(uuid.uuid4())})

    assert resp.status_code == 201, "conta desconhecida não pode derrubar a publicação"
    # Conta default: `BufferClient()` sem nenhum argumento, exatamente como
    # antes de contas extras existirem.
    mock_buf.assert_called_once_with()


# ── GET /health por conta ───────────────────────────────────────────────


async def test_health_with_unknown_account_is_404(client):
    resp = await client.get(f"/health?account_id={uuid.uuid4()}")
    assert resp.status_code == 404


async def test_health_without_account_id_unaffected(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


# ── Rampa de aquecimento: datas persistindo por conta ───────────────────


async def test_warmup_dates_persist_and_appear_in_list(client):
    account_id = str(uuid.uuid4())
    await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_rampa",
            "buffer_access_token": "tok",
            "tiktok_channel_id": "chan_tiktok",
            "tiktok_warmup_started_on": "2026-09-01",
        },
    )
    listed = (await client.get("/accounts")).json()
    row = next(a for a in listed if a["account_id"] == account_id)
    assert row["tiktok_warmup_started_on"] == "2026-09-01"
    assert row["youtube_warmup_started_on"] is None


async def test_upsert_without_optional_fields_preserves_existing_values(client):
    """Reenviar `POST /accounts` só para trocar o token não pode apagar
    `youtube_channel_id` nem a rampa em andamento por omissão no corpo."""
    account_id = str(uuid.uuid4())
    await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_completa",
            "buffer_access_token": "tok-v1",
            "tiktok_channel_id": "chan_tiktok",
            "youtube_channel_id": "chan_youtube",
            "tiktok_warmup_started_on": "2026-09-01",
            "youtube_warmup_started_on": "2026-09-03",
        },
    )

    resp = await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_completa",
            "buffer_access_token": "tok-v2",
            "tiktok_channel_id": "chan_tiktok",
        },
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["youtube_channel_id"] == "chan_youtube"
    assert data["tiktok_warmup_started_on"] == "2026-09-01"
    assert data["youtube_warmup_started_on"] == "2026-09-03"


async def test_upsert_explicit_null_clears_a_warmup_date(client):
    """Contraste com o teste acima: `null` explícito no corpo apaga de
    propósito — só a omissão é que preserva."""
    account_id = str(uuid.uuid4())
    await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_null",
            "buffer_access_token": "tok",
            "tiktok_channel_id": "chan_tiktok",
            "tiktok_warmup_started_on": "2026-09-01",
        },
    )
    resp = await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_null",
            "buffer_access_token": "tok",
            "tiktok_channel_id": "chan_tiktok",
            "tiktok_warmup_started_on": None,
        },
    )
    assert resp.json()["tiktok_warmup_started_on"] is None


# ── Rampa aplicada em POST /schedule ─────────────────────────────────────


async def test_schedule_passes_a_ramp_aware_cap_when_account_is_warming(client):
    """Wiring: com `tiktok_warmup_started_on` cadastrado, `next_available_slot`
    recebe uma função (não mais um `int`), e ela já reflete o primeiro degrau."""
    account_id = str(uuid.uuid4())
    await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_rampa_tiktok",
            "buffer_access_token": "tok",
            "tiktok_channel_id": "chan_tiktok",
            "tiktok_warmup_started_on": date.today().isoformat(),
        },
    )

    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.next_available_slot") as mock_slot,
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
        mock_slot.return_value = datetime.now(timezone.utc)

        await client.post("/schedule", json={**SAMPLE_REQUEST, "account_id": account_id})

    posts_per_day_arg = mock_slot.call_args[0][1]
    assert callable(posts_per_day_arg)
    assert posts_per_day_arg(date.today()) == 1  # 1x7,2x7,3 — primeiro degrau


async def test_schedule_passes_a_plain_int_when_the_account_is_not_warming(client):
    account_id = str(uuid.uuid4())
    await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_madura",
            "buffer_access_token": "tok",
            "tiktok_channel_id": "chan_tiktok",
        },
    )

    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.next_available_slot") as mock_slot,
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
        mock_slot.return_value = datetime.now(timezone.utc)

        await client.post("/schedule", json={**SAMPLE_REQUEST, "account_id": account_id})

    assert mock_slot.call_args[0][1] == 3  # config.ini [posting] posts_per_day, sem rampa


async def test_schedule_skips_youtube_over_its_own_days_warmup_cap(client, youtube_on):
    account_id = str(uuid.uuid4())
    fixed_slot = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_rampa_youtube",
            "buffer_access_token": "tok",
            "tiktok_channel_id": "chan_tiktok",
            "youtube_channel_id": "chan_youtube",
            "youtube_warmup_started_on": "2026-09-10",
        },
    )
    pending_yt = [{"id": "yt_ja_publicado", "due_at": int(fixed_slot.timestamp())}]

    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.next_available_slot", return_value=fixed_slot),
        patch(
            "src.tiktok_poster.api.routes.schedule.generate_presigned_url",
            new_callable=AsyncMock,
            return_value="https://r2.example.com/video.mp4",
        ),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=pending_yt)
        mock_buf.return_value.create_post = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)

        resp = await client.post(
            "/schedule",
            json={**SAMPLE_REQUEST, "account_id": account_id, "youtube_title": "Título"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["youtube_update_id"] is None
    assert data["youtube_error"] == "fora do teto de aquecimento do dia"
    assert data["youtube_enabled"] is True
    # Só o TikTok chamou create_post — o YouTube nunca chegou lá.
    assert mock_buf.return_value.create_post.await_count == 1


async def test_schedule_youtube_ramp_does_not_apply_to_continuations(client, youtube_on):
    """Unidade do teto = história: uma parte 2+ nunca é barrada pela rampa do
    YouTube, mesma exceção que `continuation_slot` já faz no TikTok."""
    account_id = str(uuid.uuid4())
    follows_at = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    continuation = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)
    await client.post(
        "/accounts",
        json={
            "account_id": account_id,
            "slug": "conta_serie",
            "buffer_access_token": "tok",
            "tiktok_channel_id": "chan_tiktok",
            "youtube_channel_id": "chan_youtube",
            "youtube_warmup_started_on": "2026-09-10",
        },
    )
    # Fila do YouTube já no teto do dia (1) — se a rampa se aplicasse aqui, o
    # segundo post seria pulado. Como é continuação, não deve ser.
    pending_yt = [{"id": "yt_parte_1", "due_at": int(follows_at.timestamp())}]
    create_mock = AsyncMock(side_effect=[BUFFER_CREATE_RESPONSE, {"updates": [{"id": "yt_parte_2"}]}])

    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.continuation_slot", return_value=continuation),
        patch(
            "src.tiktok_poster.api.routes.schedule.generate_presigned_url",
            new_callable=AsyncMock,
            return_value="https://r2.example.com/video.mp4",
        ),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=pending_yt)
        mock_buf.return_value.create_post = create_mock

        resp = await client.post(
            "/schedule",
            json={
                **SAMPLE_REQUEST,
                "account_id": account_id,
                "youtube_title": "Título",
                "follows_at": follows_at.isoformat(),
            },
        )

    assert resp.json()["youtube_update_id"] == "yt_parte_2"
