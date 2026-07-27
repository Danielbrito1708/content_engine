import os
from unittest.mock import MagicMock, patch

import pytest

from src.core import settings
from src.core.config import TTSEnvSettings
from src.tts_service.tts.edge import EdgeTTSClient


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
    assert settings.env.tts_rate == "+15%"


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
    assert TTSEnvSettings().tts_rate == "+15%"


# ── health ───────────────────────────────────────────────────────


async def test_health_exposes_rate(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["rate"] == os.environ.get("TTS_RATE", "+15%")
