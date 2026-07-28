"""Which background clip a given part gets.

The pipeline used to point every render at one fixed key, so every video in the
channel shared the same footage from the same second of the same file. Cutting
the source into clips and rotating between them is what makes two consecutive
posts not look like the same video with different words on top.
"""

import hashlib


def pick_background(keys: list[str], run_id: str, part_number: int) -> str:
    """Pick a clip for one part, deterministically.

    Deterministic rather than random for two reasons. A part re-rendered after a
    restart has to come back with the same footage, or a retry silently produces
    a different video than the one already reviewed. And the seed includes the
    part number, so the parts of a single run never land on the same clip — which
    is exactly where repetition would be most visible, in a series posted
    back-to-back.
    """
    if not keys:
        raise ValueError("no background clips available")

    seed = f"{run_id}:{part_number}".encode()
    index = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(keys)
    return keys[index]
