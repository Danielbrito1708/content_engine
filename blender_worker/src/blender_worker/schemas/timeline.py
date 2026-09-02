from __future__ import annotations

from pydantic import BaseModel, Field


class TimelineValidateRequest(BaseModel):
    #: Raw YAML text — the document being edited, not yet published anywhere.
    template: str
    #: Asset name -> duration in SECONDS (what a human types). Converted to
    #: frames using the parsed template's own `canvas.fps` before resolving.
    #: A duration-less asset (e.g. an image referenced only via `until:`)
    #: still needs a placeholder value to be treated as "supplied" — see
    #: `resolve_timeline`'s docstring; `0` is a valid placeholder.
    inputs: dict[str, float] = Field(default_factory=dict)
    #: Merged on top of the template's own `flags:` block.
    flags: dict[str, bool] = Field(default_factory=dict)


class TimelineIssue(BaseModel):
    location: str
    message: str


class ResolvedClipOut(BaseModel):
    track: str
    channel: int
    role: str
    type: str
    frame_start: int
    frame_end: int | None = None
    repeats: list[int] = Field(default_factory=list)
    fade_start: int | None = None
    extra: dict = Field(default_factory=dict)


class TimelineValidateResponse(BaseModel):
    ok: bool
    timeline_end: int | None = None
    anchors: dict[str, int] = Field(default_factory=dict)
    clips: list[ResolvedClipOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[TimelineIssue] = Field(default_factory=list)
