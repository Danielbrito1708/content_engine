import hashlib


def _pool_order(pool: list[str], seed: str) -> list[str]:
    """The pool, reordered per post but reproducible.

    Sorting by ``sha256(seed:tag)`` and not by ``random.shuffle`` for the same
    reason the background picker is deterministic: reagendar a mesma parte tem
    de devolver a mesma legenda, ou um retry publica um texto diferente do que
    foi revisado. Seeds diferentes dão ordens sem relação entre si.
    """
    return sorted(pool, key=lambda tag: hashlib.sha256(f"{seed}:{tag}".encode()).hexdigest())


def select_hashtags(
    hints: list[str],
    mandatory: list[str],
    pool: list[str],
    max_total: int = 8,
    seed: str | None = None,
) -> list[str]:
    """Builds the final hashtag list for a post.

    Priority: mandatory (always included) → hints from LLM classification
    → pool fallback if hints don't fill the slots.
    Deduplicates preserving order. Total capped at max_total.

    ``seed`` reordena o **pool** por post. Sem ele o preenchimento vinha sempre
    do topo da lista, então todo post que não tinha hints suficientes terminava
    com as mesmas `#viral #foryou #foryoupage` na mesma ordem — junto com as
    obrigatórias, era uma legenda de cauda idêntica repetida post após post. As
    `mandatory` e os `hints` continuam na ordem em que chegam: aquelas são uma
    escolha explícita e estes descrevem a história.
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

    for tag in (_pool_order(pool, seed) if seed else pool):
        if len(result) >= max_total:
            break
        _add(tag)

    return result


def compose_caption(cta: str, hashtags: list[str], part_number: int, total_parts: int) -> str:
    """Builds the full TikTok post caption."""
    parts_label = f" (Parte {part_number}/{total_parts})" if total_parts > 1 else ""
    tags_str = " ".join(hashtags)
    return f"{cta}{parts_label}\n\n{tags_str}"
