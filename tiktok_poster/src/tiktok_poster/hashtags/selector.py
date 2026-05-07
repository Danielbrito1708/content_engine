def select_hashtags(
    hints: list[str],
    mandatory: list[str],
    pool: list[str],
    max_total: int = 8,
) -> list[str]:
    """Builds the final hashtag list for a post.

    Priority: mandatory (always included) → hints from LLM classification
    → pool fallback if hints don't fill the slots.
    Deduplicates preserving order. Total capped at max_total.
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(tag: str) -> None:
        normalized = tag if tag.startswith("#") else f"#{tag}"
        if normalized.lower() not in seen and len(result) < max_total:
            seen.add(normalized.lower())
            result.append(normalized)

    for tag in mandatory:
        _add(tag)

    for tag in hints:
        _add(tag)

    for tag in pool:
        if len(result) >= max_total:
            break
        _add(tag)

    return result


def compose_caption(cta: str, hashtags: list[str], part_number: int, total_parts: int) -> str:
    """Builds the full TikTok post caption."""
    parts_label = f" (Parte {part_number}/{total_parts})" if total_parts > 1 else ""
    tags_str = " ".join(hashtags)
    return f"{cta}{parts_label}\n\n{tags_str}"
