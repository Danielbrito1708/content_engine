"""Which music track matches the mood of a given story.

The pipeline used to point every render at one fixed track, so a story about a
funeral and a story about a happy ending shared the same bed. The `llm_service`
now classifies the story's mood; this module picks a track for it.
"""

import hashlib

#: Extensões aceitas como faixa de música.
TRACK_SUFFIXES = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")


def is_track(key: str) -> bool:
    """Se a chave é áudio, e não outro objeto qualquer sob o prefixo do mood."""
    return key.lower().endswith(TRACK_SUFFIXES)


def pick_music(keys: list[str], run_id: str) -> str:
    """Escolhe uma faixa entre os candidatos de um mood.

    Determinístico (`sha256(run_id)`, não `hash()`, que é salgado por
    processo): um run re-renderizado depois de um restart volta com a mesma
    trilha, em vez de trocar a cama sonora debaixo de um vídeo que continua
    sendo o mesmo. Sem contagem de uso — ao contrário do fundo, a biblioteca
    por mood tende a ter poucas faixas, e a mesma música tocando em vídeos
    consecutivos não é o defeito visível que o mesmo clipe de fundo é.
    """
    if not keys:
        raise ValueError("no music tracks available")

    ordered = sorted(keys)
    index = int(hashlib.sha256(f"{run_id}".encode()).hexdigest()[:8], 16) % len(ordered)
    return ordered[index]
