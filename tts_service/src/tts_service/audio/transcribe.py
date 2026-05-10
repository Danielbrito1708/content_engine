import os
import tempfile

from faster_whisper import WhisperModel

_model: WhisperModel | None = None


def _get_model(model_name: str) -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(model_name, device="cpu", compute_type="int8")
    return _model


def transcribe_to_srt(audio_bytes: bytes, language: str = "pt", model_name: str = "base") -> bytes:
    model = _get_model(model_name)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        segments, _ = model.transcribe(tmp_path, language=language)
        return _to_srt(list(segments))
    finally:
        os.unlink(tmp_path)


def _to_srt(segments) -> bytes:
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt(seg.start)} --> {_fmt(seg.end)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _fmt(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int((t % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
