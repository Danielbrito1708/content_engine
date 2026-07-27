import os
import subprocess
import tempfile

# start_duration controls how long non-silence must be observed before the filter
# begins outputting. Using min_silence_s here would silently drop short speech clips
# (e.g. 300ms clip never reaches a 500ms non-silence threshold → nothing output).
# Use a small fixed value (50ms) so output starts as soon as speech is confirmed.
#
# stop_duration controls how long a silence must be to qualify for removal — this IS
# the user-facing min_silence_ms parameter.
_START_CONFIRM_S = 0.05

# Voices carry nothing useful below 80 Hz — only room rumble and plosive thump, which
# eat headroom that loudnorm would otherwise give to the voice.
_HIGHPASS_HZ = 80

_TRUE_PEAK_DB = -1.5
_LOUDNESS_RANGE = 11


def build_filter_chain(
    *,
    trim_silence: bool,
    min_silence_ms: int,
    silence_thresh_db: int,
    normalize: bool,
    loudness_target_lufs: int,
    sample_rate: int,
) -> list[str]:
    """Filters for one ffmpeg pass, in order. Empty means nothing to do.

    Trimming runs before loudnorm on purpose: loudnorm measures the whole stream, so
    leading/trailing silence would drag the measured loudness down and push the voice
    louder than the target.
    """
    filters: list[str] = []

    if trim_silence:
        min_silence_s = min_silence_ms / 1000.0
        thresh = f"{silence_thresh_db}dB"
        lead = (
            f"silenceremove=start_periods=1:"
            f"start_duration={_START_CONFIRM_S}:start_threshold={thresh}"
        )
        # 1. strip leading silence, 2. areverse + strip trailing + reverse back,
        # 3. strip internal silences.
        filters += [lead, "areverse", lead, "areverse"]
        filters.append(
            f"silenceremove=stop_periods=-1:"
            f"stop_duration={min_silence_s}:stop_threshold={thresh}"
        )

    if normalize:
        filters.append(f"highpass=f={_HIGHPASS_HZ}")
        filters.append(
            f"loudnorm=I={loudness_target_lufs}:TP={_TRUE_PEAK_DB}:LRA={_LOUDNESS_RANGE}"
        )
        # Single-pass loudnorm outputs at 192 kHz regardless of input; without this the
        # encoder would inherit that rate and the file would be needlessly huge.
        filters.append(f"aresample={sample_rate}")

    return filters


def process_audio(
    audio_bytes: bytes,
    *,
    trim_silence: bool = True,
    min_silence_ms: int = 500,
    silence_thresh_db: int = -40,
    normalize: bool = True,
    loudness_target_lufs: int = -16,
    bitrate: str = "192k",
    sample_rate: int = 48000,
) -> bytes:
    """Trim silence and normalize loudness in a single ffmpeg pass, encoding MP3 once.

    Everything happens in one pass because each MP3→MP3 round trip is another lossy
    generation. When no filter is requested the input is returned untouched rather than
    re-encoded, for the same reason.

    Pure: takes MP3 bytes, returns MP3 bytes, no side effects beyond temp files.
    """
    filters = build_filter_chain(
        trim_silence=trim_silence,
        min_silence_ms=min_silence_ms,
        silence_thresh_db=silence_thresh_db,
        normalize=normalize,
        loudness_target_lufs=loudness_target_lufs,
        sample_rate=sample_rate,
    )
    if not filters:
        return audio_bytes

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        in_path = tmp.name

    out_path = in_path + "_processed.mp3"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", in_path,
                "-af", ",".join(filters),
                # Explicit encode settings. Without -b:a, ffmpeg picks a libmp3lame default
                # from the sample rate — measured at 64 kbps for 48 kHz mono, which would
                # quietly crush a 192 kbps source to a third of its bitrate on every pass.
                "-c:a", "libmp3lame",
                "-b:a", bitrate,
                "-ar", str(sample_rate),
                "-ac", "1",
                out_path,
            ],
            capture_output=True,
            check=True,
        )
        with open(out_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(in_path)
        if os.path.exists(out_path):
            os.unlink(out_path)
