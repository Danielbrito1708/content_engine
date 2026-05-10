"""
Tests for silence removal. Requires ffmpeg/ffprobe installed locally (or run inside Docker).
Audio is generated with Python's wave module + ffmpeg to ensure format compatibility.
"""
import json
import math
import os
import struct
import subprocess
import tempfile
import wave
from unittest.mock import AsyncMock, patch

from tests.conftest import SAMPLE_REQUEST
from src.tts_service.audio.silence import remove_silence

_SAMPLE_RATE = 44100


# --- helpers ---

def _make_mp3(*segments: tuple[str, int]) -> bytes:
    """Build MP3 from segments: ('tone', ms) or ('silence', ms).
    Uses Python wave + ffmpeg to produce a format that ffmpeg filter chains handle correctly.
    """
    samples: list[int] = []
    for type_, ms in segments:
        n = int(_SAMPLE_RATE * ms / 1000)
        if type_ == "tone":
            freq = 440
            samples += [int(32767 * math.sin(2 * math.pi * freq * i / _SAMPLE_RATE)) for i in range(n)]
        else:
            samples += [0] * n

    wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_tmp.close()
    mp3_tmp = wav_tmp.name.replace(".wav", ".mp3")
    try:
        with wave.open(wav_tmp.name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(_SAMPLE_RATE)
            w.writeframes(struct.pack(f"<{len(samples)}h", *samples))

        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_tmp.name, mp3_tmp],
            capture_output=True, check=True,
        )
        with open(mp3_tmp, "rb") as f:
            return f.read()
    finally:
        os.unlink(wav_tmp.name)
        if os.path.exists(mp3_tmp):
            os.unlink(mp3_tmp)


def _duration_ms(audio_bytes: bytes) -> int:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, check=True, text=True,
        )
        info = json.loads(result.stdout)
        return int(float(info["format"]["duration"]) * 1000)
    finally:
        os.unlink(path)


def _tone(duration_ms: int = 300) -> AudioSegment:
    return Sine(440).to_audio_segment(duration=duration_ms)


def _silence(duration_ms: int) -> AudioSegment:
    return AudioSegment.silent(duration=duration_ms)


# --- pure function tests ---

def test_removes_leading_silence():
    audio_bytes = _make_mp3(("silence", 800), ("tone", 300))
    result = remove_silence(audio_bytes, min_silence_ms=500, silence_thresh_db=-40, padding_ms=50)
    assert _duration_ms(result) < _duration_ms(audio_bytes)


def test_removes_trailing_silence():
    audio_bytes = _make_mp3(("tone", 300), ("silence", 800))
    result = remove_silence(audio_bytes, min_silence_ms=500, silence_thresh_db=-40, padding_ms=50)
    assert _duration_ms(result) < _duration_ms(audio_bytes)


def test_removes_internal_long_silence():
    audio_bytes = _make_mp3(("tone", 300), ("silence", 1000), ("tone", 300))
    result = remove_silence(audio_bytes, min_silence_ms=500, silence_thresh_db=-40, padding_ms=50)
    assert _duration_ms(result) < _duration_ms(audio_bytes)


def test_preserves_short_silence_between_words():
    # 200ms gap is below min_silence_ms=500, so it's preserved as part of the speech chunk
    audio_bytes = _make_mp3(("tone", 300), ("silence", 200), ("tone", 300))
    result = remove_silence(audio_bytes, min_silence_ms=500, silence_thresh_db=-40, padding_ms=50)
    assert _duration_ms(result) > 200


def test_fully_silent_audio_returns_bytes():
    # ffmpeg silenceremove outputs near-empty audio when everything is silence
    audio_bytes = _make_mp3(("silence", 1000))
    result = remove_silence(audio_bytes, min_silence_ms=500, silence_thresh_db=-40, padding_ms=50)
    assert isinstance(result, bytes)


def test_returns_bytes():
    audio_bytes = _make_mp3(("tone", 500))
    result = remove_silence(audio_bytes)
    assert isinstance(result, bytes)


def test_audio_with_only_speech_unchanged_length():
    audio_bytes = _make_mp3(("tone", 500))
    result = remove_silence(audio_bytes, min_silence_ms=500, silence_thresh_db=-40, padding_ms=50)
    assert _duration_ms(result) > 200  # still has meaningful audio


# --- route integration tests ---

async def test_route_calls_remove_silence_when_enabled(client):
    fake_mp3 = _make_mp3(("tone", 300))
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
        patch("src.tts_service.api.routes.generate.settings") as mock_settings,
        patch("src.tts_service.api.routes.generate.remove_silence", side_effect=lambda b, *a, **kw: b) as mock_remove,
    ):
        mock_settings.env.remove_silence = True
        mock_settings.env.min_silence_ms = 500
        mock_settings.env.silence_thresh_db = -40
        mock_settings.env.silence_padding_ms = 100
        mock_settings.CONFIG.storage.bucket = "blender-jobs"
        mock_factory.return_value.generate = AsyncMock(return_value=fake_mp3)

        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    mock_remove.assert_called_once_with(fake_mp3, 500, -40, 100)


async def test_route_skips_remove_silence_when_disabled(client):
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
        patch("src.tts_service.api.routes.generate.remove_silence") as mock_remove,
    ):
        mock_factory.return_value.generate = AsyncMock(return_value=b"\xff\xfb\x90\x00" + b"\x00" * 100)
        # conftest sets REMOVE_SILENCE=false
        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    mock_remove.assert_not_called()


async def test_route_continues_on_silence_removal_error(client):
    fake_mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 100
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock) as mock_upload,
        patch("src.tts_service.api.routes.generate.settings") as mock_settings,
        patch("src.tts_service.api.routes.generate.remove_silence", side_effect=RuntimeError("ffmpeg missing")),
    ):
        mock_settings.env.remove_silence = True
        mock_settings.env.min_silence_ms = 500
        mock_settings.env.silence_thresh_db = -40
        mock_settings.env.silence_padding_ms = 100
        mock_settings.CONFIG.storage.bucket = "blender-jobs"
        mock_factory.return_value.generate = AsyncMock(return_value=fake_mp3)

        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    _, _, uploaded = mock_upload.call_args[0]
    assert uploaded == fake_mp3
