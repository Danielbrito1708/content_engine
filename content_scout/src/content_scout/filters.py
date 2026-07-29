import hashlib
import re
import unicodedata

from src.content_scout.sources.base import Candidate

#: Everything that is not a letter or a digit. Reposts are retyped, re-punctuated
#: and re-paragraphed constantly; keeping punctuation in the hash would make two
#: copies of the same story hash differently over a changed comma.
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")

#: How much of the body the fingerprint covers. A repost usually keeps the story
#: and drops or adds a trailing "EDIT:" / "thanks for the awards" block, so
#: hashing the whole text would let a comment-driven edit defeat the match. The
#: opening is also the part that is actually reused verbatim.
FINGERPRINT_CHARS = 1000


def normalize_for_fingerprint(text: str) -> str:
    """Reduce a body to the letters and digits of its opening, lowercased.

    Accents are stripped too: the same story crossposted between a Portuguese
    and an English sub — or simply retyped without diacritics — must land on the
    same fingerprint, and no downstream consumer reads this string.
    """
    folded = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return _NON_ALNUM_RE.sub("", stripped)[:FINGERPRINT_CHARS]


def content_fingerprint(text: str) -> str | None:
    """Stable hash of a body, or ``None`` when there is nothing to fingerprint.

    ``None`` rather than the hash of an empty string: an empty normalization
    means every such candidate would collide with every other, and the second
    one would be discarded as a repost of the first. A body that normalizes to
    nothing is not a duplicate of anything.
    """
    normalized = normalize_for_fingerprint(text)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def evaluate(candidate: Candidate, min_chars: int, max_chars: int) -> str | None:
    """Return a skip reason, or ``None`` when the candidate is usable.

    Only cheap, deterministic checks live here. Safety is a separate step and a
    judgement about context — see ``clients/llm.py`` — so it does not belong in a
    function that runs over every candidate on every cycle.

    Length bounds exist on both ends: too short has no story to tell, and too
    long means the LLM would have to cut so much that what airs is barely the
    original post.
    """
    if candidate.char_count < min_chars:
        return f"too_short:{candidate.char_count}"
    if candidate.char_count > max_chars:
        return f"too_long:{candidate.char_count}"
    return None
