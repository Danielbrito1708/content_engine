def text_to_srt(text: str, words_per_minute: int = 150) -> bytes:
    """Convert plain text to SRT subtitle format with estimated timing."""
    words = text.split()
    seconds_per_word = 60 / words_per_minute
    chunk_size = 8

    chunks = [words[i : i + chunk_size] for i in range(0, len(words), chunk_size)]
    parts: list[str] = []
    t = 0.0
    for idx, chunk in enumerate(chunks, start=1):
        duration = len(chunk) * seconds_per_word
        line = " ".join(chunk)
        parts.append(f"{idx}\n{_fmt(t)} --> {_fmt(t + duration)}\n{line}\n")
        t += duration

    return "\n".join(parts).encode("utf-8")


def _fmt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
