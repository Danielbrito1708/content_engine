"""Pontua posts reais do Reddit contra o `/story-quality` de verdade.

Ferramenta de **calibragem**, não parte do pipeline. Reusa o `RedditSource` e o
mesmo recorte (`story_excerpt_chars`) que o scout usa, então o que chega no
endpoint é byte a byte o que o pipeline mandaria — sem história inventada.

Uso::

    # com o llm_service de pé na 8010 (container efêmero do worktree)
    PYTHONPATH="$PWD" poetry run python scripts/score_real_posts.py \\
        --subreddits desabafos,relacionamentos --out scored.json

Ver `docs/story_quality_calibration.md` para o que fazer com o resultado.
"""
import argparse
import asyncio
import json
import sys
from collections import Counter

import httpx

from src.content_scout.sources.reddit import RedditSource


def _outrage(row: dict, args) -> int:
    """Revolta não julgada vale o ponto neutro, como no ciclo."""
    return row["outrage"] if row["outrage"] is not None else args.outrage_neutral


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subreddits", default="desabafos,relacionamentos")
    parser.add_argument("--time-filter", default="week")
    parser.add_argument("--limit", type=int, default=15, help="por subreddit")
    parser.add_argument(
        "--excerpt",
        type=int,
        default=700,
        help="deve espelhar [scout] story_excerpt_chars",
    )
    parser.add_argument("--endpoint", default="http://localhost:8010/story-quality")
    parser.add_argument("--out", default="scored.json")
    parser.add_argument(
        "--outrage-weight",
        type=int,
        default=2,
        help="peso da revolta na ordenação, igual ao [scout] outrage_weight",
    )
    parser.add_argument(
        "--outrage-neutral",
        type=int,
        default=6,
        help="nota de revolta atribuída a quem o modelo não pontuou",
    )
    args = parser.parse_args()

    source = RedditSource(
        base_url="https://www.reddit.com",
        subreddits=[s.strip() for s in args.subreddits.split(",") if s.strip()],
        time_filter=args.time_filter,
        limit_per_subreddit=args.limit,
        user_agent="content_engine/0.1 (content_scout)",
        # O rate limit do Reddit é por cliente, não por endpoint.
        request_delay=60.0,
    )
    candidates = await source.fetch()
    print(f"fetched: {len(candidates)}", file=sys.stderr)
    if not candidates:
        return

    payload = {
        "items": [
            {"index": i, "title": c.title, "opening": c.text[: args.excerpt]}
            for i, c in enumerate(candidates)
        ]
    }

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(args.endpoint, json=payload)
        resp.raise_for_status()
        results = resp.json()["results"]

    out = []
    for verdict in results:
        candidate = candidates[verdict["index"]]
        out.append(
            {
                "score": verdict["score"],
                "hook": verdict["hook"],
                "outrage": verdict.get("outrage"),
                "villain": verdict.get("villain", False),
                "hook_line": verdict.get("hook_line"),
                "reason": verdict.get("reason"),
                "origin": candidate.origin,
                "title": candidate.title,
                "url": candidate.url,
                "opening": candidate.text[: args.excerpt],
                "char_count": candidate.char_count,
            }
        )

    # Mesma chave que o ciclo usa para escolher — ver `rank_by_story`. Ordenar
    # por `score` aqui mostraria uma fila diferente da que produz vídeo.
    out.sort(key=lambda r: -(args.outrage_weight * _outrage(r, args) + r["score"]))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print(f"scored: {len(out)} -> {args.out}", file=sys.stderr)
    print(
        f"distribuição: {dict(sorted(Counter(r['score'] for r in out).items()))}",
        file=sys.stderr,
    )
    print(f"hook=true: {sum(1 for r in out if r['hook'])}/{len(out)}", file=sys.stderr)
    print(
        f"revolta: {dict(sorted(Counter(r['outrage'] for r in out).items(), key=lambda kv: (kv[0] is None, kv[0])))}",
        file=sys.stderr,
    )
    print(f"villain=true: {sum(1 for r in out if r['villain'])}/{len(out)}", file=sys.stderr)
    for row in out:
        outrage = "--" if row["outrage"] is None else f"{row['outrage']:2d}"
        print(
            f"  {row['score']:2d}  revolta={outrage}  vilao={str(row['villain']):5s}  "
            f"hook={str(row['hook']):5s}  {row['origin']:20s} {row['title'][:60]}",
            file=sys.stderr,
        )


asyncio.run(main())
