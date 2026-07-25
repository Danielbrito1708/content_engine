from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


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
