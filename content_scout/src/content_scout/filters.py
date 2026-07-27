from src.content_scout.sources.base import Candidate


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
