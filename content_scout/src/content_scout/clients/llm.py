from dataclasses import dataclass

import httpx
import structlog

from src.core import settings

log = structlog.get_logger(__name__)

#: Tag written to ``SeenItem.story_tag``. Mutually exclusive, worst first: a weak
#: story is the headline even when its title happens to hook, so it outranks
#: ``no_hook``. ``has_hook`` keeps the raw answer either way, so nothing is lost.
TAG_WEAK = "weak_storytelling"
TAG_NO_HOOK = "no_hook"
TAG_STRONG = "strong"


@dataclass(frozen=True)
class Verdict:
    safe: bool
    category: str | None = None
    reason: str | None = None

    def as_skip_reason(self) -> str:
        return f"unsafe:{self.category or 'unspecified'}"


class ModerationError(RuntimeError):
    """The verdict could not be obtained.

    Deliberately distinct from an unsafe verdict: "we could not check" must never
    collapse into either "safe" (publishes unchecked content) or "unsafe"
    (permanently discards a good story over a transient outage).
    """


class ModerationClient:
    def __init__(self):
        self._base = settings.CONFIG.services.llm_url
        self._timeout = settings.CONFIG.scout.moderation_timeout

    async def check(self, title: str, text: str) -> Verdict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/moderate", json={"title": title, "text": text}
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ModerationError(str(exc)) from exc

        return Verdict(
            safe=bool(data.get("safe")),
            category=data.get("category"),
            reason=data.get("reason"),
        )


@dataclass(frozen=True)
class StoryScore:
    """How well one candidate opens, as judged by ``llm_service``.

    ``hook``, ``score`` and ``outrage`` answer separate questions — whether the
    title or first lines promise a payoff, how strong the storytelling is, and how
    much indignation the story provokes — so a candidate can have one without the
    others.
    """

    hook: bool
    score: int
    #: Outrage potential, 0–10. ``None`` when the model did not answer it, which
    #: is why it is not an ``int`` with a zero default: an unanswered field must
    #: sort at the neutral point, not at the bottom. See ``rank_value``.
    outrage: int | None = None
    #: Whether the story has someone plainly in the wrong. Not part of the
    #: ranking arithmetic — ``outrage`` already prices it in, and multiplying the
    #: same signal twice would only make the weight harder to reason about. It is
    #: stored so the audit trail can answer "does a villain actually predict the
    #: numbers?" without re-reading every post.
    villain: bool = False
    hook_line: str | None = None
    reason: str | None = None

    def rank_value(self, outrage_neutral: int, outrage_weight: int) -> int:
        """The number the cycle sorts on, higher first.

        ``outrage_weight`` multiplies the outrage score before adding the
        storytelling one, which makes the trade-off explicit rather than a
        priority order: at weight 2 a story has to be about five points better
        told to beat a story two points angrier. Ordering strictly by outrage
        would let a 10/3 beat a 9/9, and a story nobody can sit through does not
        collect the comments the outrage was selected for.

        An unanswered ``outrage`` scores at ``outrage_neutral`` for the same
        reason an unscored candidate does — see ``rank_by_story``.
        """
        outrage = self.outrage if self.outrage is not None else outrage_neutral
        return outrage_weight * outrage + self.score

    def tag(self, min_score: int) -> str:
        """The label stored on the audit row and forwarded to the refiner.

        Derived rather than asked of the model, so the line between weak and
        strong can be moved against real data — the same reason the length bounds
        live in config instead of in the filter.
        """
        if self.score < min_score:
            return TAG_WEAK
        return TAG_STRONG if self.hook else TAG_NO_HOOK

    def as_metadata(self, min_score: int) -> dict:
        """The judgement as it travels to the orchestrator, and on to ``/refine``.

        The refiner is told to open on a strong hook; knowing this post has none
        is the difference between polishing an existing hook and having to build
        one. ``hook_line`` rides along when there is one, since that is the line
        worth keeping up front.
        """
        payload = {
            "story_tag": self.tag(min_score),
            "story_score": self.score,
            "has_hook": self.hook,
            "has_villain": self.villain,
        }
        if self.outrage is not None:
            payload["outrage_score"] = self.outrage
        if self.hook_line:
            payload["hook_line"] = self.hook_line
        return payload


class StoryQualityClient:
    """Batched quality scoring for a whole cycle's candidates.

    Unlike moderation, a failure here is **not** fatal to the cycle. Moderation
    gates publication, so "could not check" must stop everything; this only
    *orders* candidates and labels them, so losing it costs the ranking signal and
    nothing else. Hence ``score()`` returns an empty mapping instead of raising —
    the caller falls back to the source's own ranking and records no tag.
    """

    def __init__(self):
        self._base = settings.CONFIG.services.llm_url
        cfg = settings.CONFIG.scout
        self._timeout = cfg.story_timeout
        self._excerpt_chars = cfg.story_excerpt_chars

    def _payload(self, candidates: list) -> dict:
        """Title plus the opening of each body, indexed by list position.

        Only the opening is sent: 30 full posts would be ~180k characters, which
        defeats the point of batching. It is also the honest input for the
        question — a viewer decides in the first seconds, from exactly this much.
        """
        return {
            "items": [
                {
                    "index": i,
                    "title": candidate.title,
                    "opening": candidate.text[: self._excerpt_chars],
                }
                for i, candidate in enumerate(candidates)
            ]
        }

    async def score(self, candidates: list) -> dict[str, StoryScore]:
        """Map ``external_id`` to its score, keyed so order can be changed freely.

        Candidates the model did not answer for are simply absent from the map —
        distinct from a low score, exactly as ``comment_count IS NULL`` is
        distinct from zero. A missing entry means "not judged", never "bad".
        """
        if not candidates:
            return {}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/story-quality", json=self._payload(candidates)
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("story_quality_unavailable", error=str(exc))
            return {}

        scores: dict[str, StoryScore] = {}
        for entry in data.get("results") or []:
            index = entry.get("index")
            if not isinstance(index, int) or not 0 <= index < len(candidates):
                continue
            try:
                score = int(entry["score"])
            except (KeyError, TypeError, ValueError):
                continue
            # Outrage degrades on its own: a verdict that answers the
            # storytelling score but not this one is still a usable verdict, so a
            # missing or unparseable value becomes "not judged" instead of
            # dropping the whole entry.
            try:
                outrage = int(entry["outrage"])
            except (KeyError, TypeError, ValueError):
                outrage = None

            scores[candidates[index].external_id] = StoryScore(
                hook=bool(entry.get("hook")),
                score=score,
                outrage=outrage,
                villain=bool(entry.get("villain")),
                hook_line=entry.get("hook_line") or None,
                reason=entry.get("reason") or None,
            )

        return scores
