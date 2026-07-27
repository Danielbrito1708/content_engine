import json
import os
import subprocess
import tempfile

# Fallbacks for when ffprobe cannot read the source (corrupt header, odd container).
# Matching the source is always preferred — see probe_source.
_FALLBACK_SAMPLE_RATE = 48000
_FALLBACK_BITRATE = "192k"

# start_duration controls how long non-silence must be observed before the filter
# begins outputting. Using max_pause_s here would silently drop short speech clips
# (e.g. 300ms clip never reaches a 500ms non-silence threshold → nothing output).
# Use a small fixed value (50ms) so output starts as soon as speech is confirmed.
_START_CONFIRM_S = 0.05

# Voices carry nothing useful below 80 Hz — only room rumble and plosive thump, which
# eat headroom that loudnorm would otherwise give to the voice.
_HIGHPASS_HZ = 80

_TRUE_PEAK_DB = -1.5
_LOUDNESS_RANGE = 11


def build_filter_chain(
    *,
    trim_silence: bool,
    max_pause_ms: int,
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
        max_pause_s = max_pause_ms / 1000.0
        thresh = f"{silence_thresh_db}dB"
        lead = (
            f"silenceremove=start_periods=1:"
            f"start_duration={_START_CONFIRM_S}:start_threshold={thresh}"
        )
        # 1. strip leading silence, 2. areverse + strip trailing + reverse back,
        # 3. cap internal pauses.
        filters += [lead, "areverse", lead, "areverse"]
        filters.append(
            f"silenceremove=stop_periods=-1:"
            f"stop_duration={max_pause_s}:stop_threshold={thresh}"
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


def probe_source(path: str) -> tuple[int, str]:
    """(sample_rate, bitrate) of the input, so the output can match it.

    Upsampling never adds bandwidth. Encoding a 24 kHz / 48 kbps edge-tts file at
    48 kHz / 192 kbps measured 3.5x larger for byte-identical audible content, so
    "match the source" is the right default and an explicit override is opt-in.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_entries", "stream=sample_rate,bit_rate", path],
            capture_output=True, check=True, text=True,
        )
        stream = json.loads(result.stdout)["streams"][0]
        rate = int(stream["sample_rate"])
        # bit_rate is absent on some VBR streams; fall back rather than guess low.
        bits = int(stream.get("bit_rate") or 0)
        return rate, (f"{bits // 1000}k" if bits else _FALLBACK_BITRATE)
    except Exception:
        return _FALLBACK_SAMPLE_RATE, _FALLBACK_BITRATE


def process_audio(
    audio_bytes: bytes,
    *,
    trim_silence: bool = True,
    max_pause_ms: int = 200,
    silence_thresh_db: int = -40,
    normalize: bool = True,
    loudness_target_lufs: int = -16,
    bitrate: str | None = None,
    sample_rate: int | None = None,
) -> bytes:
    """Trim silence and normalize loudness in a single ffmpeg pass, encoding MP3 once.

    Everything happens in one pass because each MP3→MP3 round trip is another lossy
    generation. When no filter is requested the input is returned untouched rather than
    re-encoded, for the same reason.

    `max_pause_ms` is a **ceiling, not a trigger**. ffmpeg's silenceremove copies audio
    until `stop_duration` of silence has gone by and only then stops, so the parameter is
    at once the detection threshold and the amount of silence left behind. A pause
    shorter than it survives untouched; every longer one — 600ms or 6s — comes out at
    exactly max_pause_ms. Measured on a 7.5s clip holding a 1.5s and a 3.0s pause: both
    ended at 0.52s under the old 500ms default, which is why the narration sounded gappy.

    `stop_silence` is deliberately not used: it *adds* to what is kept (measured, 0.1
    gave 0.62s of residual pause), so it can only lengthen pauses — under a low ceiling
    it would stretch a pause past its original length.

    `bitrate` and `sample_rate` default to whatever the source already is. Forcing them
    higher cannot add information — it only inflates the file.

    Pure: takes MP3 bytes, returns MP3 bytes, no side effects beyond temp files.
    """
    if not (trim_silence or normalize):
        return audio_bytes

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        in_path = tmp.name

    out_path = in_path + "_processed.mp3"
    try:
        if bitrate is None or sample_rate is None:
            probed_rate, probed_bitrate = probe_source(in_path)
            sample_rate = sample_rate if sample_rate is not None else probed_rate
            bitrate = bitrate if bitrate is not None else probed_bitrate

        filters = build_filter_chain(
            trim_silence=trim_silence,
            max_pause_ms=max_pause_ms,
            silence_thresh_db=silence_thresh_db,
            normalize=normalize,
            loudness_target_lufs=loudness_target_lufs,
            sample_rate=sample_rate,
        )
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
