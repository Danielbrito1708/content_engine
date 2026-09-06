"""Pydantic models for the declarative timeline YAML (`version: 2`).

See `docs/edicao_declarativa.md` for the format's rationale and a full
example. This module only validates *shape* and catches typos in field
names (`extra="forbid"` almost everywhere) — cross-references (`$hook`,
`$card_end`, an input that does not exist) are `resolver.py`'s job, because
checking them needs the anchors evaluated in declaration order, which is
exactly what the resolver already does.

Visual-only properties (typography, colours, fade-frame counts on
non-timing filters) are modelled but not interpreted here — this module
resolves *time*, not pixels. Fase 2's `bpy`-side executor is what reads
them.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class Canvas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int
    height: int
    fps: int
    tail: str = "0s"
    # Legacy absolute frame number — see resolver.py's module docstring.
    # Only used when a timeline somehow has no content track at all.
    fallback_end: int


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["video", "audio", "image", "srt"]
    required: bool = True


class Conditional(BaseModel):
    """`{when: <flag>, then: <expr>, else: <expr>}` — the only branching the
    format allows. See docs/edicao_declarativa.md § "Condicionais mínimas"."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    when: str
    then: str
    else_: str = Field(alias="else")


# An anchor (or any position-like clip field) is either a bare expression
# string or a two-way conditional on a flag.
AnchorValue = Union[str, Conditional]


class VolumeFadeFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["volume_fade"]
    to: float = 0.0
    duration: str
    anchor: Literal["timeline_end"]


class VisualFilter(BaseModel):
    """`fade_in` / `fade_out` / `rise` / `outline` — read by the Fase 2
    executor, not by this resolver. Kept loose (`extra="allow"`) because
    their shape is a bpy-side concern, not a timing one."""

    model_config = ConfigDict(extra="allow")
    type: Literal["fade_in", "fade_out", "rise", "outline"]


Filter = Union[VolumeFadeFilter, VisualFilter]


class ClipBase(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    source: str | None = None
    # Required for every clip except `subtitles`, which uses `sync_to`
    # instead (resolver.py accepts either) because it reads better on a clip
    # whose timing follows another clip rather than a fixed point.
    start: AnchorValue | None = None
    # "source" (default) means "as long as the input asset itself is" — a
    # sentinel, not an expression, so it is handled directly in resolver.py
    # rather than by expr.py.
    duration: Literal["source"] | str = "source"
    until: AnchorValue | None = None
    filters: list[Filter] = Field(default_factory=list)


class VideoClip(ClipBase):
    type: Literal["video"]
    loop: Literal["until_end"] | None = None
    min_frames: int = 2


class AudioClip(ClipBase):
    type: Literal["audio"]
    volume: float = 1.0
    # `$name` mirrors a boolean flag directly (`flags[name]`) — not a time
    # expression, so it does not go through expr.py. A literal `true`/`false`
    # is also valid for a clip that is unconditionally muted.
    muted: str | bool | None = None


class ImageClip(ClipBase):
    type: Literal["image"]
    y_position: float = 0.5
    fit: Literal["original"] = "original"
    blend: Literal["alpha_over"] = "alpha_over"


class SubtitlesClip(ClipBase):
    type: Literal["subtitles"]
    # Alias for `start` that reads better on a clip whose timing follows
    # another clip rather than a fixed point — resolver.py treats it exactly
    # like `start`.
    sync_to: AnchorValue | None = None
    hide_before: AnchorValue | None = None
    max_hold: str = "0.4s"
    fade: str = "3f"
    rise: dict | None = None
    style: dict = Field(default_factory=dict)


Clip = Annotated[
    Union[VideoClip, AudioClip, ImageClip, SubtitlesClip],
    Field(discriminator="type"),
]


class Track(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    channel: int
    role: Literal["content", "bed"]
    # An input name (without `$`) that must be present for the whole track
    # to exist — the optional hook/card tracks use this.
    when_present: str | None = None
    clips: list[Clip]


class TimelineDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[2]
    canvas: Canvas
    inputs: dict[str, InputSpec]
    flags: dict[str, bool] = Field(default_factory=dict)
    anchors: dict[str, AnchorValue] = Field(default_factory=dict)
    tracks: list[Track]
