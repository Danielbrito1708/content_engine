from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Comment:
    """One reaction to a candidate, normalized across sources.

    No score field: Reddit's feeds carry none (see ``RedditSource``), and
    inventing a placeholder would let downstream code sort by a number that does
    not exist. Ordering is the source's own, preserved as ``position``.
    """

    external_id: str
    author: str
    text: str
    position: int
    published: str = ""


@dataclass(frozen=True)
class CommentThread:
    """Reactions to a candidate plus how many the source reported.

    ``total`` is the count the source made visible, which is a floor rather than
    a census — deleted, collapsed and paged-out replies never appear. It is a
    popularity signal, not a metric to report as exact.
    """

    total: int
    comments: list["Comment"] = field(default_factory=list)


@dataclass(frozen=True)
class Candidate:
    """A piece of text that could become a video script.

    Every source normalizes to this shape before anything downstream sees it —
    the same contract ``docs/vision.md`` sets for the trigger: plain text plus
    metadata, regardless of where it came from.
    """

    source: str
    external_id: str
    origin: str
    title: str
    text: str
    url: str
    extra: dict = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)

    def to_metadata(self) -> dict:
        """Metadata handed to the orchestrator, which forwards it to the LLM prompt."""
        return {
            "source": self.source,
            "origin": self.origin,
            "title": self.title,
            "url": self.url,
            **self.extra,
        }


@runtime_checkable
class Source(Protocol):
    """Contract every content source implements.

    ``name`` is stored on ``SeenItem.source``; ``fetch`` returns raw candidates
    with no deduplication or filtering applied — those are the scout's job, so
    that every source gets the same treatment.
    """

    name: str

    async def fetch(self) -> list[Candidate]: ...


@runtime_checkable
class CommentCapableSource(Protocol):
    """Optional capability: a source that can also return reactions to a candidate.

    Kept separate from ``Source`` so a source without comments (or one whose API
    makes them too expensive) stays a valid source. The scout probes for this
    with ``isinstance`` and simply skips enrichment when it is not implemented.
    """

    name: str

    async def fetch_comments(self, candidate: Candidate) -> CommentThread | None: ...
