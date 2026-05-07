import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import FAKE_MP3, SAMPLE_REQUEST


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["provider"] == "edge"
    assert "pt-BR" in data["voice"]


async def test_generate_returns_audio_key(client):
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
    ):
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    data = resp.json()
    assert data["audio_key"] == "audio/550e8400-e29b-41d4-a716-446655440000/part_1.mp3"


async def test_generate_key_format_for_part_2(client):
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
    ):
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        resp = await client.post("/generate", json={**SAMPLE_REQUEST, "part_number": 2})

    assert resp.json()["audio_key"].endswith("part_2.mp3")


async def test_generate_passes_text_to_tts(client):
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
    ):
        mock_tts = AsyncMock(return_value=FAKE_MP3)
        mock_factory.return_value.generate = mock_tts
        await client.post("/generate", json=SAMPLE_REQUEST)

    mock_tts.assert_called_once_with(SAMPLE_REQUEST["text"])


async def test_generate_uploads_to_correct_bucket(client):
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock) as mock_upload,
    ):
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        await client.post("/generate", json=SAMPLE_REQUEST)

    bucket, key, data = mock_upload.call_args[0]
    assert bucket == "blender-jobs"
    assert key == "audio/550e8400-e29b-41d4-a716-446655440000/part_1.mp3"
    assert data == FAKE_MP3


async def test_generate_returns_502_on_tts_failure(client):
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
    ):
        mock_factory.return_value.generate = AsyncMock(side_effect=RuntimeError("network error"))
        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 502
    assert "TTS error" in resp.json()["detail"]


async def test_generate_returns_502_on_upload_failure(client):
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock) as mock_upload,
    ):
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        mock_upload.side_effect = RuntimeError("minio unavailable")
        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 502
    assert "Upload error" in resp.json()["detail"]


async def test_factory_edge(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "edge")
    from src.tts_service.tts.edge import EdgeTTSClient
    from src.tts_service.tts.factory import get_tts_client
    assert isinstance(get_tts_client(), EdgeTTSClient)


async def test_factory_unknown_raises(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "invalid")
    from src.tts_service.tts.factory import get_tts_client
    with pytest.raises(ValueError, match="Unknown TTS_PROVIDER"):
        get_tts_client()


async def test_edge_client_uses_env_voice(monkeypatch):
    monkeypatch.setenv("TTS_VOICE", "pt-BR-AntonioNeural")
    from src.tts_service.tts.edge import EdgeTTSClient
    c = EdgeTTSClient()
    assert c._voice == "pt-BR-AntonioNeural"
