"""Which background clip a given part gets.

The pipeline used to point every render at one fixed key, so every video in the
channel shared the same footage from the same second of the same file. Cutting
the source into clips and rotating between them is what makes two consecutive
posts not look like the same video with different words on top.
"""

import hashlib
from collections.abc import Mapping


def pick_background(
    keys: list[str],
    run_id: str,
    part_number: int,
    used: Mapping[str, int] | None = None,
) -> str:
    """Pick a clip for one part: the least-used one, ties broken deterministically.

    ``used`` maps clip key to how many parts already went out on it. The clip
    comes from the subset with the fewest uses, so **no clip repeats until the
    whole library has been through**. This is the part that was missing: the
    picker used to be ``sha256(run_id) % len(keys)``, a draw *with replacement*
    that only looked like a rotation. Over 47 posts on a 40-clip library it
    reused a clip 20 times, the first repeat landing on the second day — which
    is the opposite of what a rotation is for.

    Ties are broken by hash rather than by list order so a fresh cycle does not
    walk the library alphabetically, which would make consecutive posts share
    consecutive footage from the same source file.

    Deterministic for two reasons. A part re-rendered after a restart has to
    come back with the same footage, or a retry silently produces a different
    video than the one already reviewed — the caller also persists the choice,
    which is what holds across a change in ``used``. And the seed includes the
    part number, so the parts of a single run never land on the same clip, which
    is exactly where repetition would be most visible, in a series posted
    back-to-back.

    A clip added to the library later starts at zero uses, so it is picked
    before anything already in rotation — new footage reaches the channel
    without waiting out the current cycle.
    """
    if not keys:
        raise ValueError("no background clips available")

    counts = used or {}
    fewest = min(counts.get(key, 0) for key in keys)
    candidates = [key for key in keys if counts.get(key, 0) == fewest]

    seed = f"{run_id}:{part_number}".encode()
    index = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(candidates)
    return candidates[index]
