"""
Tests for audio postprocessing (silence trim + loudness normalization).
Requires ffmpeg/ffprobe installed locally (or run inside Docker).
Audio is generated with Python's wave module + ffmpeg to ensure format compatibility.
"""
import json
import math
import os
import re
import struct
import subprocess
import tempfile
import wave
from unittest.mock import AsyncMock, patch

from src.core.config import TTSEnvSettings
from tests.conftest import SAMPLE_REQUEST
from tests.test_generate import _mock_transcription
from src.tts_service.audio.postprocess import (
    build_filter_chain,
    probe_source,
    process_audio,
)

_SAMPLE_RATE = 44100


# --- helpers ---

def _make_mp3(*segments: tuple[str, int], amplitude: float = 1.0) -> bytes:
    """Build MP3 from segments: ('tone', ms) or ('silence', ms).
    Uses Python wave + ffmpeg to produce a format that ffmpeg filter chains handle correctly.
    """
    samples: list[int] = []
    peak = int(32767 * amplitude)
    for type_, ms in segments:
        n = int(_SAMPLE_RATE * ms / 1000)
        if type_ == "tone":
            freq = 440
            samples += [int(peak * math.sin(2 * math.pi * freq * i / _SAMPLE_RATE)) for i in range(n)]
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


def _make_24khz_mp3(*segments: tuple[str, int]) -> bytes:
    """Same shape as what the edge provider emits: 24 kHz / 48 kbps mono."""
    src = _make_mp3(*segments)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(src)
        in_path = f.name
    out_path = in_path + "_24k.mp3"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", in_path, "-ar", "24000", "-ac", "1",
             "-c:a", "libmp3lame", "-b:a", "48k", out_path],
            capture_output=True, check=True,
        )
        with open(out_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(in_path)
        if os.path.exists(out_path):
            os.unlink(out_path)


def _with_tempfile(audio_bytes: bytes, fn):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        return fn(path)
    finally:
        os.unlink(path)


def _probe(audio_bytes: bytes) -> dict:
    def run(path):
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, check=True, text=True,
        )
        return json.loads(result.stdout)
    return _with_tempfile(audio_bytes, run)


def _duration_ms(audio_bytes: bytes) -> int:
    return int(float(_probe(audio_bytes)["format"]["duration"]) * 1000)


def _sample_rate(audio_bytes: bytes) -> int:
    return int(_probe(audio_bytes)["streams"][0]["sample_rate"])


def _integrated_lufs(audio_bytes: bytes) -> float:
    """Measured integrated loudness via ffmpeg's ebur128 filter."""
    def run(path):
        result = subprocess.run(
            ["ffmpeg", "-i", path, "-af", "ebur128", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        # The summary block at the end reports "I:  -16.0 LUFS".
        matches = re.findall(r"I:\s+(-?[\d.]+)\s+LUFS", result.stderr)
        assert matches, f"no loudness in ffmpeg output: {result.stderr[-500:]}"
        return float(matches[-1])
    return _with_tempfile(audio_bytes, run)


def _pause_durations_ms(audio_bytes: bytes, thresh_db: int = -40) -> list[int]:
    """Durations of the silent stretches left inside the audio, via ffmpeg silencedetect."""
    def run(path: str) -> list[int]:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", path,
             "-af", f"silencedetect=n={thresh_db}dB:d=0.05", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        return [
            int(float(line.split("silence_duration:")[1].strip()) * 1000)
            for line in result.stderr.splitlines()
            if "silence_duration:" in line
        ]
    return _with_tempfile(audio_bytes, run)


_TRIM_ONLY = dict(trim_silence=True, normalize=False)


# --- filter chain ---

def test_no_filters_when_everything_disabled():
    assert build_filter_chain(
        trim_silence=False, max_pause_ms=500, silence_thresh_db=-40,
        normalize=False, loudness_target_lufs=-16, sample_rate=48000,
    ) == []


def test_chain_has_silenceremove_when_trimming():
    chain = build_filter_chain(
        trim_silence=True, max_pause_ms=500, silence_thresh_db=-40,
        normalize=False, loudness_target_lufs=-16, sample_rate=48000,
    )
    assert any("silenceremove" in f for f in chain)
    assert not any("loudnorm" in f for f in chain)


def test_chain_has_loudnorm_and_resample_when_normalizing():
    chain = build_filter_chain(
        trim_silence=False, max_pause_ms=500, silence_thresh_db=-40,
        normalize=True, loudness_target_lufs=-16, sample_rate=48000,
    )
    assert any("loudnorm=I=-16" in f for f in chain)
    assert any("highpass" in f for f in chain)
    # Without this, single-pass loudnorm would emit 192 kHz.
    assert chain[-1] == "aresample=48000"


def test_chain_trims_before_normalizing():
    # Leading/trailing silence would drag the measured loudness down.
    chain = build_filter_chain(
        trim_silence=True, max_pause_ms=500, silence_thresh_db=-40,
        normalize=True, loudness_target_lufs=-16, sample_rate=48000,
    )
    first_trim = next(i for i, f in enumerate(chain) if "silenceremove" in f)
    first_norm = next(i for i, f in enumerate(chain) if "loudnorm" in f)
    assert first_trim < first_norm


def test_target_lufs_is_configurable():
    chain = build_filter_chain(
        trim_silence=False, max_pause_ms=500, silence_thresh_db=-40,
        normalize=True, loudness_target_lufs=-23, sample_rate=48000,
    )
    assert any("loudnorm=I=-23" in f for f in chain)


# --- silence trimming ---

def test_removes_leading_silence():
    audio_bytes = _make_mp3(("silence", 800), ("tone", 300))
    result = process_audio(audio_bytes, max_pause_ms=500, silence_thresh_db=-40, **_TRIM_ONLY)
    assert _duration_ms(result) < _duration_ms(audio_bytes)


def test_removes_trailing_silence():
    audio_bytes = _make_mp3(("tone", 300), ("silence", 800))
    result = process_audio(audio_bytes, max_pause_ms=500, silence_thresh_db=-40, **_TRIM_ONLY)
    assert _duration_ms(result) < _duration_ms(audio_bytes)


def test_removes_internal_long_silence():
    audio_bytes = _make_mp3(("tone", 300), ("silence", 1000), ("tone", 300))
    result = process_audio(audio_bytes, max_pause_ms=500, silence_thresh_db=-40, **_TRIM_ONLY)
    assert _duration_ms(result) < _duration_ms(audio_bytes)


def test_preserves_short_silence_between_words():
    # 200ms gap is below max_pause_ms=500, so it's preserved as part of the speech chunk
    audio_bytes = _make_mp3(("tone", 300), ("silence", 200), ("tone", 300))
    result = process_audio(audio_bytes, max_pause_ms=500, silence_thresh_db=-40, **_TRIM_ONLY)
    assert _duration_ms(result) > 200


def test_fully_silent_audio_returns_bytes():
    # ffmpeg silenceremove outputs near-empty audio when everything is silence
    audio_bytes = _make_mp3(("silence", 1000))
    result = process_audio(audio_bytes, max_pause_ms=500, silence_thresh_db=-40, **_TRIM_ONLY)
    assert isinstance(result, bytes)


def test_audio_with_only_speech_keeps_content():
    audio_bytes = _make_mp3(("tone", 500))
    result = process_audio(audio_bytes, max_pause_ms=500, silence_thresh_db=-40, **_TRIM_ONLY)
    assert _duration_ms(result) > 200


# --- max_pause_ms is a ceiling, not a trigger ---
# ffmpeg's silenceremove only stops copying after stop_duration of silence has already
# gone by, so whatever is passed is exactly what stays behind. These tests pin that down —
# it is the reason the narration used to sound gappy under the old 500ms default.

def test_internal_pause_is_capped_at_max_pause_ms():
    audio_bytes = _make_mp3(("tone", 500), ("silence", 3000), ("tone", 500))
    result = process_audio(audio_bytes, max_pause_ms=200, silence_thresh_db=-40, **_TRIM_ONLY)
    pauses = _pause_durations_ms(result)
    assert len(pauses) == 1
    assert 150 <= pauses[0] <= 300, f"expected ~200ms pause, got {pauses[0]}ms"


def test_pauses_of_different_lengths_collapse_to_the_same_ceiling():
    audio_bytes = _make_mp3(
        ("tone", 500), ("silence", 1500), ("tone", 500), ("silence", 3000), ("tone", 500)
    )
    result = process_audio(audio_bytes, max_pause_ms=200, silence_thresh_db=-40, **_TRIM_ONLY)
    pauses = _pause_durations_ms(result)
    assert len(pauses) == 2
    assert abs(pauses[0] - pauses[1]) <= 30, f"pauses diverged: {pauses}"


def test_lower_ceiling_leaves_less_silence_than_the_old_default():
    audio_bytes = _make_mp3(("tone", 500), ("silence", 3000), ("tone", 500))
    tight = process_audio(audio_bytes, max_pause_ms=200, silence_thresh_db=-40, **_TRIM_ONLY)
    loose = process_audio(audio_bytes, max_pause_ms=500, silence_thresh_db=-40, **_TRIM_ONLY)
    assert _pause_durations_ms(tight)[0] < _pause_durations_ms(loose)[0]
    assert _duration_ms(tight) < _duration_ms(loose)


def test_default_ceiling_is_200ms():
    audio_bytes = _make_mp3(("tone", 500), ("silence", 3000), ("tone", 500))
    result = process_audio(audio_bytes, **_TRIM_ONLY)
    assert 150 <= _pause_durations_ms(result)[0] <= 300


def test_pause_shorter_than_ceiling_survives_intact():
    audio_bytes = _make_mp3(("tone", 500), ("silence", 120), ("tone", 500))
    before = _pause_durations_ms(audio_bytes)
    after = _pause_durations_ms(process_audio(audio_bytes, max_pause_ms=200, **_TRIM_ONLY))
    assert before and after
    assert abs(before[0] - after[0]) <= 30, f"{before} -> {after}"


def test_ceiling_never_lengthens_a_pause():
    # a 300ms pause under a 500ms ceiling must not be padded up to 500ms
    audio_bytes = _make_mp3(("tone", 500), ("silence", 300), ("tone", 500))
    pauses = _pause_durations_ms(process_audio(audio_bytes, max_pause_ms=500, **_TRIM_ONLY))
    assert pauses[0] <= 350, f"pause grew to {pauses[0]}ms"


# --- encoding / quality ---

def test_untouched_when_no_filter_requested():
    # Re-encoding for nothing would add a lossy generation, so the bytes come back as-is.
    audio_bytes = _make_mp3(("tone", 300))
    assert process_audio(audio_bytes, trim_silence=False, normalize=False) is audio_bytes


def test_output_uses_requested_sample_rate():
    # Guards the loudnorm gotcha: single-pass loudnorm emits 192 kHz unless resampled.
    audio_bytes = _make_mp3(("tone", 2000))
    result = process_audio(audio_bytes, trim_silence=False, normalize=True, sample_rate=48000)
    assert _sample_rate(result) == 48000


def test_output_respects_configured_bitrate():
    audio_bytes = _make_mp3(("tone", 2000))
    result = process_audio(audio_bytes, trim_silence=False, normalize=True, bitrate="192k")
    bit_rate = int(_probe(result)["format"]["bit_rate"])
    assert 170_000 < bit_rate < 210_000


# --- matching the source (the default) ---

def test_probe_source_reads_rate_and_bitrate():
    audio_bytes = _make_mp3(("tone", 1000))
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        rate, bitrate = probe_source(path)
    finally:
        os.unlink(path)
    assert rate == _SAMPLE_RATE
    assert bitrate.endswith("k")


def test_probe_source_falls_back_on_unreadable_input():
    # Corrupt input must not crash the pipeline — it degrades to the fallback.
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"not audio at all")
        path = f.name
    try:
        assert probe_source(path) == (48000, "192k")
    finally:
        os.unlink(path)


def test_defaults_do_not_upsample_a_24khz_source():
    # The edge provider emits 24 kHz; forcing 48 kHz added no bandwidth and measured
    # 3.5x larger, so matching the source is the default.
    src = _make_24khz_mp3(("tone", 2000))
    assert _sample_rate(src) == 24000
    result = process_audio(src, trim_silence=False, normalize=True)
    assert _sample_rate(result) == 24000


def test_defaults_do_not_inflate_bitrate():
    src = _make_24khz_mp3(("tone", 2000))
    src_bitrate = int(_probe(src)["format"]["bit_rate"])
    result = process_audio(src, trim_silence=False, normalize=True)
    out_bitrate = int(_probe(result)["format"]["bit_rate"])
    assert out_bitrate < src_bitrate * 1.5


def test_explicit_values_still_override_the_source():
    src = _make_24khz_mp3(("tone", 2000))
    result = process_audio(
        src, trim_silence=False, normalize=True, sample_rate=48000, bitrate="192k"
    )
    assert _sample_rate(result) == 48000


def test_normalize_raises_quiet_audio_toward_target():
    quiet = _make_mp3(("tone", 3000), amplitude=0.03)
    before = _integrated_lufs(quiet)
    result = process_audio(quiet, trim_silence=False, normalize=True, loudness_target_lufs=-16)
    after = _integrated_lufs(result)
    assert after > before + 5
    assert abs(after - (-16)) < 3


def test_normalize_lowers_hot_audio_toward_target():
    hot = _make_mp3(("tone", 3000), amplitude=1.0)
    before = _integrated_lufs(hot)
    result = process_audio(hot, trim_silence=False, normalize=True, loudness_target_lufs=-16)
    after = _integrated_lufs(result)
    assert after < before
    assert abs(after - (-16)) < 3


# --- route integration tests ---

def _processing_settings(mock_settings, *, trim: bool, normalize: bool):
    mock_settings.env.remove_silence = trim
    mock_settings.env.max_pause_ms = 500
    mock_settings.env.silence_thresh_db = -40
    mock_settings.env.normalize_audio = normalize
    mock_settings.env.loudness_target_lufs = -16
    mock_settings.env.audio_bitrate = "192k"
    mock_settings.env.audio_sample_rate = 48000
    mock_settings.CONFIG.storage.bucket = "blender-jobs"


async def test_route_processes_audio_when_enabled(client):
    fake_mp3 = _make_mp3(("tone", 300))
    transcribe, upload_srt = _mock_transcription()
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
        patch("src.tts_service.api.routes.generate.settings") as mock_settings,
        patch(
            "src.tts_service.api.routes.generate.process_audio",
            side_effect=lambda b, **kw: b,
        ) as mock_process,
        transcribe,
        upload_srt,
    ):
        _processing_settings(mock_settings, trim=True, normalize=True)
        mock_factory.return_value.generate = AsyncMock(return_value=fake_mp3)

        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    mock_process.assert_called_once_with(
        fake_mp3,
        trim_silence=True,
        max_pause_ms=500,
        silence_thresh_db=-40,
        normalize=True,
        loudness_target_lufs=-16,
        bitrate="192k",
        sample_rate=48000,
    )


async def test_route_still_normalizes_when_trimming_is_off(client):
    # Normalization is independent of silence removal — turning one off must not
    # skip the other.
    fake_mp3 = _make_mp3(("tone", 300))
    transcribe, upload_srt = _mock_transcription()
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
        patch("src.tts_service.api.routes.generate.settings") as mock_settings,
        patch(
            "src.tts_service.api.routes.generate.process_audio",
            side_effect=lambda b, **kw: b,
        ) as mock_process,
        transcribe,
        upload_srt,
    ):
        _processing_settings(mock_settings, trim=False, normalize=True)
        mock_factory.return_value.generate = AsyncMock(return_value=fake_mp3)

        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    assert mock_process.call_args.kwargs["normalize"] is True
    assert mock_process.call_args.kwargs["trim_silence"] is False


async def test_route_skips_processing_when_all_disabled(client):
    transcribe, upload_srt = _mock_transcription()
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
        patch("src.tts_service.api.routes.generate.process_audio") as mock_process,
        transcribe,
        upload_srt,
    ):
        mock_factory.return_value.generate = AsyncMock(return_value=b"\xff\xfb\x90\x00" + b"\x00" * 100)
        # conftest sets REMOVE_SILENCE=false and NORMALIZE_AUDIO=false
        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    mock_process.assert_not_called()


async def test_route_continues_on_processing_error(client):
    fake_mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 100
    transcribe, upload_srt = _mock_transcription()
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock) as mock_upload,
        patch("src.tts_service.api.routes.generate.settings") as mock_settings,
        patch(
            "src.tts_service.api.routes.generate.process_audio",
            side_effect=RuntimeError("ffmpeg missing"),
        ),
        transcribe,
        upload_srt,
    ):
        _processing_settings(mock_settings, trim=True, normalize=True)
        mock_factory.return_value.generate = AsyncMock(return_value=fake_mp3)

        resp = await client.post("/generate", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    _, _, uploaded = mock_upload.call_args[0]
    assert uploaded == fake_mp3


# --- MAX_PAUSE_MS config, with the deprecated MIN_SILENCE_MS alias ---

def test_max_pause_defaults_to_200(monkeypatch):
    monkeypatch.delenv("MAX_PAUSE_MS", raising=False)
    monkeypatch.delenv("MIN_SILENCE_MS", raising=False)
    assert TTSEnvSettings().max_pause_ms == 200


def test_max_pause_reads_its_own_var(monkeypatch):
    monkeypatch.setenv("MAX_PAUSE_MS", "150")
    monkeypatch.delenv("MIN_SILENCE_MS", raising=False)
    assert TTSEnvSettings().max_pause_ms == 150


def test_deprecated_min_silence_ms_is_still_honoured(monkeypatch):
    # A tuned .env from before the rename must keep working — the number always
    # meant the same thing, so ignoring it would change behaviour silently.
    monkeypatch.delenv("MAX_PAUSE_MS", raising=False)
    monkeypatch.setenv("MIN_SILENCE_MS", "350")
    assert TTSEnvSettings().max_pause_ms == 350


def test_max_pause_wins_over_the_deprecated_alias(monkeypatch):
    monkeypatch.setenv("MAX_PAUSE_MS", "150")
    monkeypatch.setenv("MIN_SILENCE_MS", "500")
    assert TTSEnvSettings().max_pause_ms == 150
