import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core import settings
from src.core.config import TTSEnvSettings
from src.tts_service.tts.edge import EdgeTTSClient
from tests.conftest import FAKE_MP3, SAMPLE_REQUEST


def _fake_communicate(chunks):
    """Builds a mock edge_tts.Communicate whose stream() yields `chunks`."""

    async def stream():
        for c in chunks:
            yield c

    instance = MagicMock()
    instance.stream = stream
    return instance


# ── EdgeTTSClient ────────────────────────────────────────────────


def test_client_defaults_to_configured_rate():
    assert EdgeTTSClient()._rate == settings.env.tts_rate


def test_client_accepts_explicit_rate():
    assert EdgeTTSClient(rate="+30%")._rate == "+30%"


def test_client_explicit_rate_overrides_config():
    assert EdgeTTSClient(rate="-10%")._rate != settings.env.tts_rate


async def test_generate_passes_rate_to_edge_tts():
    audio = [{"type": "audio", "data": b"\xff\xfb"}]
    with patch("src.tts_service.tts.edge.edge_tts.Communicate") as mock_comm:
        mock_comm.return_value = _fake_communicate(audio)
        await EdgeTTSClient(voice="pt-BR-ThalitaNeural", rate="+25%").generate("olá mundo")

    _, kwargs = mock_comm.call_args
    assert kwargs["rate"] == "+25%"


async def test_generate_still_returns_audio_bytes_with_rate():
    audio = [
        {"type": "audio", "data": b"\xff\xfb"},
        {"type": "WordBoundary", "data": b"ignored"},
        {"type": "audio", "data": b"\x90\x00"},
    ]
    with patch("src.tts_service.tts.edge.edge_tts.Communicate") as mock_comm:
        mock_comm.return_value = _fake_communicate(audio)
        out = await EdgeTTSClient(rate="+15%").generate("olá mundo")

    assert out == b"\xff\xfb\x90\x00"


# ── TTS_RATE config ──────────────────────────────────────────────


def test_default_rate_speeds_narration_up():
    """Narration ships faster than the raw TTS output unless overridden."""
    assert settings.env.tts_rate == "+50%"


def test_rate_read_from_env(monkeypatch):
    monkeypatch.setenv("TTS_RATE", "+40%")
    assert TTSEnvSettings().tts_rate == "+40%"


def test_rate_accepts_negative(monkeypatch):
    monkeypatch.setenv("TTS_RATE", "-20%")
    assert TTSEnvSettings().tts_rate == "-20%"


@pytest.mark.parametrize("bad", ["15%", "+15", "fast", "+15 %", "", "+1.5%"])
def test_invalid_rate_rejected(monkeypatch, bad):
    monkeypatch.setenv("TTS_RATE", bad)
    with pytest.raises(ValueError, match="TTS_RATE"):
        TTSEnvSettings()


def test_rate_defaults_when_env_absent(monkeypatch):
    monkeypatch.delenv("TTS_RATE", raising=False)
    assert TTSEnvSettings().tts_rate == "+50%"


# ── rate por request (vem do template.json) ──────────────────────


def _patched_endpoint():
    """Patches the endpoint's TTS + upload so only the rate plumbing is under test."""
    return (
        patch("src.tts_service.api.routes.generate.get_tts_client"),
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
    )


async def test_request_rate_overrides_env(client):
    factory_p, upload_p = _patched_endpoint()
    with factory_p as mock_factory, upload_p:
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        await client.post("/generate", json={**SAMPLE_REQUEST, "rate": "+40%"})

    assert mock_factory.call_args.kwargs["rate"] == "+40%"


async def test_absent_request_rate_falls_back_to_env(client):
    factory_p, upload_p = _patched_endpoint()
    with factory_p as mock_factory, upload_p:
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        await client.post("/generate", json=SAMPLE_REQUEST)

    assert mock_factory.call_args.kwargs["rate"] == settings.env.tts_rate


async def test_request_rate_accepts_negative(client):
    factory_p, upload_p = _patched_endpoint()
    with factory_p as mock_factory, upload_p:
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        await client.post("/generate", json={**SAMPLE_REQUEST, "rate": "-25%"})

    assert mock_factory.call_args.kwargs["rate"] == "-25%"


async def test_explicit_null_rate_falls_back_to_env(client):
    factory_p, upload_p = _patched_endpoint()
    with factory_p as mock_factory, upload_p:
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        await client.post("/generate", json={**SAMPLE_REQUEST, "rate": None})

    assert mock_factory.call_args.kwargs["rate"] == settings.env.tts_rate


@pytest.mark.parametrize("bad", ["15%", "+15", "rapido", "+1.5%", ""])
async def test_invalid_request_rate_returns_422(client, bad):
    resp = await client.post("/generate", json={**SAMPLE_REQUEST, "rate": bad})
    assert resp.status_code == 422


async def test_invalid_request_rate_never_reaches_tts(client):
    factory_p, upload_p = _patched_endpoint()
    with factory_p as mock_factory, upload_p:
        await client.post("/generate", json={**SAMPLE_REQUEST, "rate": "muito rapido"})

    mock_factory.assert_not_called()


# ── factory ──────────────────────────────────────────────────────


def test_factory_forwards_rate(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "edge")
    from src.tts_service.tts.factory import get_tts_client
    assert get_tts_client(rate="+35%")._rate == "+35%"


def test_factory_without_rate_uses_env(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "edge")
    from src.tts_service.tts.factory import get_tts_client
    assert get_tts_client()._rate == settings.env.tts_rate


def test_factory_forwards_rate_to_azure():
    """The template's rate must survive a provider switch, not just work on edge."""
    from types import SimpleNamespace

    from src.tts_service.tts import factory

    stub = SimpleNamespace(env=SimpleNamespace(tts_provider="azure"))
    with patch.object(factory, "settings", stub):
        client = factory.get_tts_client(rate="+35%")

    assert client._rate == "+35%"


def test_azure_ssml_carries_the_rate():
    from src.tts_service.tts.azure import build_ssml
    ssml = build_ssml("olá", "pt-BR-ThalitaNeural", "+35%")
    assert "<prosody rate='+35%'>" in ssml


# ── health ───────────────────────────────────────────────────────


async def test_health_exposes_rate(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["rate"] == os.environ.get("TTS_RATE", "+50%")
