import os
import tempfile

from faster_whisper import WhisperModel

#: Um modelo por nome. Era uma variável única, e passou a não poder ser: a
#: legenda do render roda no ``base`` e a transcrição de vídeo de terceiro roda
#: num modelo maior (áudio com música por baixo derruba o ``base``). Com cache de
#: um slot, quem chegasse segundo receberia silenciosamente o modelo do primeiro.
_models: dict[str, WhisperModel] = {}


def _get_model(model_name: str) -> WhisperModel:
    model = _models.get(model_name)
    if model is None:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        _models[model_name] = model
    return model


def transcribe_to_srt(audio_bytes: bytes, language: str = "pt", model_name: str = "base") -> bytes:
    model = _get_model(model_name)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        segments, _ = model.transcribe(tmp_path, language=language, word_timestamps=True)
        return _words_to_srt(list(segments))
    finally:
        os.unlink(tmp_path)


def transcribe_file_to_text(
    audio_path: str, language: str = "pt", model_name: str = "small"
) -> tuple[str, float]:
    """Transcreve um arquivo de áudio em texto corrido. Devolve ``(texto, duração)``.

    Difere de :func:`transcribe_to_srt` em três pontos, todos pelo mesmo motivo —
    o destino aqui é o refino, não a legenda:

    - **Texto corrido, não SRT.** Ninguém vai sincronizar isso com imagem.
    - **Recebe caminho, não bytes.** O áudio veio de um download que já está no
      disco; passar por memória só para gravar de novo num temporário seria uma
      cópia à toa de alguns MB.
    - **``vad_filter``.** Vídeo de terceiro tem trilha sonora sob a narração, e o
      detector de fala corta o que não é voz antes de o modelo tentar
      transcrever. Áudio de TTS, que é o caso do ``/generate``, não precisa.
    """
    model = _get_model(model_name)
    segments, info = model.transcribe(
        audio_path, language=language, vad_filter=True
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text, float(info.duration)


def _words_to_srt(segments) -> bytes:
    lines = []
    i = 1
    for seg in segments:
        for word in (seg.words or []):
            lines.append(str(i))
            lines.append(f"{_fmt(word.start)} --> {_fmt(word.end)}")
            lines.append(word.word.strip())
            lines.append("")
            i += 1
    return "\n".join(lines).encode("utf-8")


def _fmt(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int((t % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
