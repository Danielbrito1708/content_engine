"""Single entry point from raw YAML text to a validated `TimelineDoc`.

Every existing caller (the Fase 1/2 test suites) hand-rolled
`TimelineDoc.model_validate(yaml.safe_load(text))` themselves — this module
exists so a new caller (the Fase 3 `/timelines/validate` route, and whatever
level 2/3 preview route follows it) doesn't have to, and so the three ways
this can fail (bad YAML syntax, YAML that isn't a mapping, a schema
violation) come back as one exception type with a uniform shape instead of
`yaml.YAMLError` and `pydantic.ValidationError` leaking to the caller raw.
"""
from __future__ import annotations

from dataclasses import dataclass

import yaml
from pydantic import ValidationError

from src.blender_worker.timeline.schema import TimelineDoc


@dataclass(frozen=True)
class LoadIssue:
    #: Dotted/indexed path to the offending value, e.g. "tracks.0.clips.0.start".
    #: "<yaml>" for a syntax error, "<root>" when the document isn't a mapping —
    #: neither has a pydantic `loc` to report.
    location: str
    message: str


class TimelineLoadError(ValueError):
    """Raised instead of `yaml.YAMLError` or `pydantic.ValidationError` —
    callers only ever need to catch one type. `.errors` always has at least
    one `LoadIssue`."""

    def __init__(self, errors: list[LoadIssue]):
        self.errors = errors
        super().__init__("; ".join(f"{e.location}: {e.message}" for e in errors))


def load_timeline(text: str) -> TimelineDoc:
    """Parses and validates `text` as a timeline document. Raises
    `TimelineLoadError` on any failure — bad YAML, a non-mapping document, or
    a schema violation (unknown field, wrong type, bad clip discriminator)."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TimelineLoadError([LoadIssue("<yaml>", str(exc))]) from exc

    if not isinstance(raw, dict):
        raise TimelineLoadError([LoadIssue("<root>", "timeline document must be a YAML mapping")])

    try:
        return TimelineDoc.model_validate(raw)
    except ValidationError as exc:
        raise TimelineLoadError([
            LoadIssue(".".join(str(p) for p in e["loc"]) or "<root>", e["msg"])
            for e in exc.errors()
        ]) from exc
