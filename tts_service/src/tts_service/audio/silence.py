import os
import subprocess
import tempfile


def remove_silence(
    audio_bytes: bytes,
    min_silence_ms: int = 500,
    silence_thresh_db: int = -40,
    padding_ms: int = 100,
) -> bytes:
    """Remove silence from an MP3 using ffmpeg silenceremove.

    Silences shorter than min_silence_ms are preserved (natural speech pauses).
    padding_ms is reserved for future use — internal cut boundaries rely on the
    natural onset/offset of speech captured by the threshold.
    """
    min_silence_s = min_silence_ms / 1000.0
    thresh = f"{silence_thresh_db}dB"

    # start_duration controls how long non-silence must be observed before the filter
    # begins outputting. Using min_silence_s here would silently drop short speech clips
    # (e.g. 300ms clip never reaches a 500ms non-silence threshold → nothing output).
    # Use a small fixed value (50ms) so output starts as soon as speech is confirmed.
    #
    # stop_duration controls how long a silence must be to qualify for removal — this IS
    # the user-facing min_silence_ms parameter.
    #
    # Chain:
    #   1. strip leading silence        (start_periods=1)
    #   2. areverse + strip trailing    (reverse, strip new leading, reverse back)
    #   3. strip internal silences      (stop_periods=-1)
    start_confirm_s = 0.05
    sr_lead = f"silenceremove=start_periods=1:start_duration={start_confirm_s}:start_threshold={thresh}"
    sr_int = f"silenceremove=stop_periods=-1:stop_duration={min_silence_s}:stop_threshold={thresh}"
    af = f"{sr_lead},areverse,{sr_lead},areverse,{sr_int}"

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        in_path = tmp.name

    out_path = in_path + "_trimmed.mp3"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", in_path, "-af", af, out_path],
            capture_output=True,
            check=True,
        )
        with open(out_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(in_path)
        if os.path.exists(out_path):
            os.unlink(out_path)
