import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.conftest import FAKE_MP3, SAMPLE_REQUEST

FAKE_SRT = b"1\n00:00:00,000 --> 00:00:00,320\nVoc\xc3\xaa\n\n"


def _mock_transcription():
    """Patches for the SRT half of the route.

    FAKE_MP3 is 104 bytes of header plus zeros — real Whisper cannot decode it,
    so without these the route reaches the transcription step and returns 502.
    Every endpoint test that expects a 201 needs them.
    """
    return (
        patch("src.tts_service.api.routes.generate.transcribe_to_srt", return_value=FAKE_SRT),
        patch("src.tts_service.api.routes.generate.upload_bytes", new_callable=AsyncMock),
    )


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["provider"] == "edge"
    assert "pt-BR" in data["voice"]


async def test_generate_returns_audio_and_srt_keys(client):
    transcribe, upload_srt = _mock_transcription()
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
        transcribe,
        upload_srt,
    ):
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    data = resp.json()
    assert data["audio_key"] == "audio/550e8400-e29b-41d4-a716-446655440000/part_1.mp3"
    assert data["srt_key"] == "subs/550e8400-e29b-41d4-a716-446655440000/part_1.srt"


async def test_generate_transcribes_the_audio_that_was_uploaded(client):
    # The SRT has to describe the same bytes that go to the video, otherwise the
    # word timings drift against the narration.
    transcribe, upload_srt = _mock_transcription()
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock) as mock_upload,
        transcribe as mock_transcribe,
        upload_srt,
    ):
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        await client.post("/generate", json=SAMPLE_REQUEST)

    uploaded_audio = mock_upload.call_args[0][2]
    assert mock_transcribe.call_args[0][0] == uploaded_audio


async def test_generate_key_format_for_part_2(client):
    transcribe, upload_srt = _mock_transcription()
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
        transcribe,
        upload_srt,
    ):
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        resp = await client.post("/generate", json={**SAMPLE_REQUEST, "part_number": 2})

    assert resp.json()["audio_key"].endswith("part_2.mp3")
    assert resp.json()["srt_key"].endswith("part_2.srt")


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
    # settings.env is built once at bootstrap and is a frozen pydantic model, so
    # neither setenv() nor setattr on the field reaches the factory. Swapping the
    # module-level `settings` is the only way to exercise the guard.
    from src.tts_service.tts import factory
    monkeypatch.setattr(
        factory, "settings", SimpleNamespace(env=SimpleNamespace(tts_provider="invalid"))
    )
    with pytest.raises(ValueError, match="Unknown TTS_PROVIDER"):
        factory.get_tts_client()


async def test_edge_client_accepts_explicit_voice():
    from src.tts_service.tts.edge import EdgeTTSClient
    assert EdgeTTSClient(voice="pt-BR-AntonioNeural")._voice == "pt-BR-AntonioNeural"


async def test_edge_client_falls_back_to_configured_voice(monkeypatch):
    # settings.env is frozen and built once at bootstrap, so setenv() cannot reach it —
    # swapping the module-level `settings` is the only way to vary the default.
    from src.tts_service.tts import edge
    monkeypatch.setattr(
        edge,
        "settings",
        SimpleNamespace(env=SimpleNamespace(tts_voice="pt-BR-FranciscaNeural", tts_rate="+0%")),
    )
    assert edge.EdgeTTSClient()._voice == "pt-BR-FranciscaNeural"
