import unicodedata

from src.content_scout.sources.base import Candidate


def _normalize(text: str) -> str:
    """Casefold and strip accents so ``suicíd`` also catches ``SUICID``/``suicid``."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def find_blocked_term(candidate: Candidate, blocklist: list[str]) -> str | None:
    """Return the first blocked term present in the title or body, if any.

    This is not prudishness — TikTok removes accounts over self-harm and sexual
    abuse content, so a single bad pull can cost the channel. Matching is
    substring-based on purpose: ``suicid`` should catch every inflection.
    """
    haystack = _normalize(f"{candidate.title}\n{candidate.text}")
    for term in blocklist:
        term = term.strip()
        if term and _normalize(term) in haystack:
            return term
    return None


def evaluate(candidate: Candidate, min_chars: int, max_chars: int,
             blocklist: list[str]) -> str | None:
    """Return a skip reason, or ``None`` when the candidate is usable.

    Length bounds exist on both ends: too short has no story to tell, and too
    long means the LLM would have to cut so much that what airs is barely the
    original post.
    """
    blocked = find_blocked_term(candidate, blocklist)
    if blocked:
        return f"blocked_term:{blocked}"
    if candidate.char_count < min_chars:
        return f"too_short:{candidate.char_count}"
    if candidate.char_count > max_chars:
        return f"too_long:{candidate.char_count}"
    return None
