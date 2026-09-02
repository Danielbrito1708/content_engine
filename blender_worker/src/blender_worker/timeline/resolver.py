"""Resolves a `TimelineDoc` (parsed from the format in
`docs/edicao_declarativa.md`) into absolute frame numbers, without ever
importing `bpy`. See that document for the format itself; this module is the
"resolvedor puro" of its Fase 1.

## Coordinate system

Every computation below happens in **relative frames** — frame 0 is the very
start of the timeline, no Blender-specific offset. The VSE places its first
frame at 1 (`scripts/edit_video.py` adds `+1` to every strip start for this
reason), so the single constant `ORIGIN` is added exactly once, at the very
end, when a relative frame is turned into the `frame_start` / `frame_end`
this module reports.

This is a correctness argument, not a style choice: `max`, `+`/`-`, and the
loop in `_background_repeats` all commute with a uniform additive shift
(`max(a+k, b+k) == k + max(a, b)`), so resolving everything in relative space
and shifting once at the end gives the same results as the legacy code,
which shifts every strip individually as it is created. `tests/
test_timeline_resolver.py` proves this directly: it resolves
`templates_v2/default.yaml` and compares every number against the actual
`intro_frames` / `content_end_frame` / `background_repeats` /
`music_fade_start` functions loaded from `scripts/edit_video.py`.

One consequence: a position-type value that resolves to relative `0`
(`hide_before`'s `else: 0s` branch in `templates_v2/default.yaml`, say)
comes out as absolute `1`, not `0` — every position gets the same `ORIGIN`
shift, including the ones that happen to land on the very first frame.
Legacy code sometimes wrote a bare `0` there instead (`hide_before=0 if not
hook_muted else card_end` in `edit_video.main()`), relying on `0` being
*falsy* rather than on `0 == 1`. The two are behaviourally identical for
every real comparison in this codebase (`drop_specs_before`'s `>=` treats
`frame<=1` as "keep everything", same as `frame<=0`, because nothing on the
timeline starts before frame 1) — but it is a real, deliberate difference in
what the number *is*, not just how the legacy code chose to spell it.

One exception: `canvas.fallback_end` is a **legacy absolute frame number** —
the same meaning `template.json`'s old `frame_end` had, used only when a
timeline has no content track at all (never happens in the shipped
template; kept for parity with `content_end_frame`'s own fallback
parameter). It is emitted as-is, with no `ORIGIN` shift, because there is
nothing relative to shift it from.

## Known Fase 1 simplification

A `subtitles` clip's `duration: source` is resolved from the caller-supplied
`inputs["subtitles"]` frame count, exactly like any other source-timed clip.
The real SRT file's word-level timing is not read here — that arrives with
the Fase 2 executor, which is the thing that actually places one text strip
per word. Treating the subtitle track as "as long as the voice" is accurate
enough for *where the video ends* (voice and subtitles are meant to track
each other), which is the only thing Fase 1's resolver is responsible for.

`templates_v2/default.yaml`'s `card_end` / `narration_start` anchors only
branch on `hook_muted` — they call `after($hook)` unconditionally, in both
branches. That models the two hook modes the pipeline actually produces
today (`blender_worker/CLAUDE.md` § "Video intro"), but not the third,
older one: no hook at all (`Video.hook_voice_key is None`), which
`edit_video.intro_frames` short-circuits before ever touching the hook. A
template resolved without a `hook` input therefore raises here rather than
falling back — the `when/then/else` conditional has no way to express "and
also branch on whether this optional input showed up at all" without a
second axis, which Fase 1 does not add. Documented as a gap, not silently
worked around; revisit if a template needs to render without a hook.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.blender_worker.timeline import expr
from src.blender_worker.timeline.schema import (
    Conditional,
    SubtitlesClip,
    TimelineDoc,
    Track,
    VideoClip,
    VolumeFadeFilter,
)

#: Blender's VSE starts its first frame at 1, not 0.
ORIGIN = 1

#: Mirrors `MAX_BACKGROUND_REPEATS` in scripts/edit_video.py — the ceiling on
#: how many extra copies of a background clip may be laid down to cover the
#: timeline. Kept in sync by test_timeline_resolver.py.
MAX_BACKGROUND_REPEATS = 60


class TimelineResolutionError(ValueError):
    """A template failed to resolve — bad reference, missing input, unknown
    flag. Raised at resolution time, never mid-render."""


@dataclass
class ResolvedClip:
    track: str
    channel: int
    role: str  # "content" | "bed" — see resolver.py's module docstring, "Verified against a real render"
    type: str
    frame_start: int
    frame_end: int | None                     # None for a bed clip whose length is a loop, not a span
    repeats: list[int] = field(default_factory=list)   # absolute starts of extra loop copies (video only)
    fade_start: int | None = None              # absolute — set only by a `volume_fade` filter
    extra: dict = field(default_factory=dict)
    # The originating schema.py clip object (volume, style, y_position, ...).
    # This module never reads it — it only carries time. `timeline/payload.py`
    # is what zips it back with the frame numbers above to build the JSON a
    # render actually needs.
    clip: Any = None


@dataclass
class ResolvedTimeline:
    timeline_end: int            # absolute
    anchors: dict[str, int]      # absolute
    clips: list[ResolvedClip]
    #: Non-fatal issues found while resolving — today only "clip ends before
    #: it starts" (see `resolve_timeline`'s docstring). Never blocks
    #: resolution; a `/timelines/validate` caller sees these alongside a
    #: successful result, not instead of one.
    warnings: list[str] = field(default_factory=list)


def resolve_timeline(doc: TimelineDoc, *, inputs: dict[str, int | None], flags: dict[str, bool] | None = None) -> ResolvedTimeline:
    """`inputs` maps each declared input name to its asset's length in
    frames, or `None`/absent if the (optional) asset was not supplied. An
    input whose own duration is never used — an image referenced only via
    `until:`, never `duration: source` — still needs a non-`None` placeholder
    so presence checks (`when_present`) see it as supplied; the value itself
    is never read in that case.
    """
    flags = flags or {}
    frame_rate = doc.canvas.fps
    _check_required_inputs(doc, inputs)

    anchors_rel: dict[str, int] = {}
    for name, value in doc.anchors.items():
        try:
            anchors_rel[name] = _resolve_value(value, anchors_rel, inputs, frame_rate, None, flags)
        except (TimelineResolutionError, expr.ExprError) as exc:
            raise type(exc)(f"anchor {name!r}: {exc}") from exc

    content_clips = []
    for track in doc.tracks:
        if track.role == "content" and not _track_skipped(track, inputs):
            content_clips.extend(_resolve_content_clips(track, anchors_rel, inputs, frame_rate, flags))

    tail_frames = expr.resolve(doc.canvas.tail, anchors={}, inputs={}, frame_rate=frame_rate)

    if content_clips:
        content_end_rel = max(c["end_rel"] for c in content_clips)
        timeline_end = ORIGIN + content_end_rel + tail_frames
    else:
        timeline_end = doc.canvas.fallback_end + tail_frames

    timeline_end_rel = timeline_end - ORIGIN

    bed_clips = []
    for track in doc.tracks:
        if track.role == "bed" and not _track_skipped(track, inputs):
            bed_clips.extend(
                _resolve_bed_clips(track, anchors_rel, inputs, frame_rate, flags, timeline_end_rel)
            )

    clips = [_finalize_clip(c) for c in content_clips + bed_clips]
    anchors_abs = {name: ORIGIN + rel for name, rel in anchors_rel.items()}
    warnings = _clip_warnings(clips)
    return ResolvedTimeline(timeline_end=timeline_end, anchors=anchors_abs, clips=clips, warnings=warnings)


# --- helpers -------------------------------------------------------------


def _input_name(ref: str) -> str:
    return ref[1:] if ref.startswith("$") else ref


def _check_required_inputs(doc, inputs):
    for name, spec in doc.inputs.items():
        if spec.required and inputs.get(name) is None:
            raise TimelineResolutionError(f"required input {name!r} was not supplied")


def _track_skipped(track: Track, inputs) -> bool:
    if not track.when_present:
        return False
    return inputs.get(_input_name(track.when_present)) is None


def _resolve_value(value, anchors, inputs, frame_rate, timeline_end, flags):
    if isinstance(value, Conditional):
        if value.when not in flags:
            raise TimelineResolutionError(f"unknown flag {value.when!r} in a when/then/else")
        branch = value.then if flags[value.when] else value.else_
        return expr.resolve(branch, anchors=anchors, inputs=inputs, frame_rate=frame_rate, timeline_end=timeline_end)
    return expr.resolve(value, anchors=anchors, inputs=inputs, frame_rate=frame_rate, timeline_end=timeline_end)


def _clip_start(clip, anchors, inputs, frame_rate, timeline_end, flags):
    raw = getattr(clip, "sync_to", None) or clip.start
    if raw is None:
        raise TimelineResolutionError(f"clip {clip.type!r} on track has neither `start` nor `sync_to`")
    return _resolve_value(raw, anchors, inputs, frame_rate, timeline_end, flags)


def _clip_end(clip, start_rel, anchors, inputs, frame_rate, timeline_end, flags):
    if clip.until is not None:
        return _resolve_value(clip.until, anchors, inputs, frame_rate, timeline_end, flags)
    if clip.duration == "source":
        name = _input_name(clip.source) if clip.source else None
        if not name or inputs.get(name) is None:
            raise TimelineResolutionError(
                f"clip {clip.type!r} needs `duration: source` but its `source` "
                f"input is missing or was not supplied"
            )
        return start_rel + inputs[name]
    return start_rel + expr.resolve(
        clip.duration, anchors=anchors, inputs=inputs, frame_rate=frame_rate, timeline_end=timeline_end
    )


def _resolve_content_clips(track: Track, anchors, inputs, frame_rate, flags):
    resolved = []
    for idx, clip in enumerate(track.clips):
        try:
            start_rel = _clip_start(clip, anchors, inputs, frame_rate, None, flags)
            end_rel = _clip_end(clip, start_rel, anchors, inputs, frame_rate, None, flags)
            extra = {}
            if isinstance(clip, SubtitlesClip) and clip.hide_before is not None:
                extra["hide_before_rel"] = _resolve_value(clip.hide_before, anchors, inputs, frame_rate, None, flags)
        except (TimelineResolutionError, expr.ExprError) as exc:
            raise type(exc)(f"track {track.name!r}, clip {idx} ({clip.type!r}): {exc}") from exc
        resolved.append({
            "track": track.name, "channel": track.channel, "role": "content", "type": clip.type,
            "start_rel": start_rel, "end_rel": end_rel, "extra": extra, "clip": clip,
        })
    return resolved


def _background_repeats(clip_frames, first_start_rel, needed_end_rel, max_repeats=MAX_BACKGROUND_REPEATS):
    """Relative-space twin of `edit_video.background_repeats`. Identical
    algorithm; only the coordinate space differs, and the two are provably
    equivalent — see this module's docstring."""
    if clip_frames < 1:
        return []
    clip_frames = int(clip_frames)
    next_start = int(round(first_start_rel)) + clip_frames
    starts = []
    while next_start <= needed_end_rel and len(starts) < max_repeats:
        starts.append(next_start)
        next_start += clip_frames
    return starts


def _music_fade_start_rel(last_frame_rel, fade_frames, first_frame_rel):
    """Relative-space twin of `edit_video.music_fade_start`."""
    if fade_frames <= 0 or last_frame_rel <= first_frame_rel:
        return None
    return max(first_frame_rel, last_frame_rel - fade_frames)


def _resolve_bed_clips(track: Track, anchors, inputs, frame_rate, flags, timeline_end_rel):
    resolved = []
    for idx, clip in enumerate(track.clips):
        try:
            start_rel = _clip_start(clip, anchors, inputs, frame_rate, timeline_end_rel, flags)
            entry = {
                "track": track.name, "channel": track.channel, "role": "bed", "type": clip.type,
                "start_rel": start_rel, "end_rel": None, "extra": {}, "clip": clip,
            }

            if isinstance(clip, VideoClip) and clip.loop == "until_end":
                name = _input_name(clip.source) if clip.source else None
                clip_frames = inputs.get(name) if name else None
                if clip_frames is None:
                    raise TimelineResolutionError("video clip with `loop: until_end` needs a resolved source duration")
                entry["repeats_rel"] = _background_repeats(clip_frames, start_rel, timeline_end_rel)

            for f in clip.filters:
                if isinstance(f, VolumeFadeFilter):
                    fade_frames = expr.resolve(
                        f.duration, anchors=anchors, inputs=inputs, frame_rate=frame_rate, timeline_end=timeline_end_rel
                    )
                    entry["fade_start_rel"] = _music_fade_start_rel(timeline_end_rel, fade_frames, start_rel)
        except (TimelineResolutionError, expr.ExprError) as exc:
            raise type(exc)(f"track {track.name!r}, clip {idx} ({clip.type!r}): {exc}") from exc

        resolved.append(entry)
    return resolved


def _finalize_clip(c) -> ResolvedClip:
    def to_abs(rel):
        return None if rel is None else ORIGIN + rel

    extra = {}
    for key, value in c.get("extra", {}).items():
        if key.endswith("_rel"):
            extra[key[: -len("_rel")]] = to_abs(value)
        else:
            extra[key] = value

    return ResolvedClip(
        track=c["track"],
        channel=c["channel"],
        role=c["role"],
        type=c["type"],
        frame_start=to_abs(c["start_rel"]),
        frame_end=to_abs(c["end_rel"]),
        repeats=[to_abs(r) for r in c.get("repeats_rel", [])],
        fade_start=to_abs(c.get("fade_start_rel")),
        extra=extra,
        clip=c["clip"],
    )


def _clip_warnings(clips: list[ResolvedClip]) -> list[str]:
    """"Clip ends before it starts" — cheap to catch, but not fatal: level 1's
    duration is a known approximation (`resolver.py`'s module docstring,
    "Known Fase 1 simplification"), so a template that looks broken under a
    caller-supplied placeholder duration may be fine against the real asset.
    Only ever fires on `content` clips — `_resolve_bed_clips` never sets
    `end_rel`, so `frame_end` is always `None` on a bed clip."""
    warnings = []
    for c in clips:
        if c.frame_end is not None and c.frame_end < c.frame_start:
            warnings.append(
                f"track {c.track!r} clip {c.type!r}: ends before it starts "
                f"(frame_start={c.frame_start}, frame_end={c.frame_end})"
            )
    return warnings


def resolve_flag_ref(value: str | bool | None, flags: dict[str, bool]) -> bool:
    """Resolves a `muted`-style field: `$name` mirrors `flags[name]`
    directly, a bare bool is used as-is, `None` means `False`. Not a time
    expression — deliberately bypasses expr.py, which only knows anchors and
    inputs, not flags. See `AudioClip.muted` in schema.py."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if value.startswith("$"):
        name = value[1:]
        if name not in flags:
            raise TimelineResolutionError(f"unknown flag {name!r}")
        return bool(flags[name])
    raise TimelineResolutionError(f"{value!r} is neither a bool nor a $flag reference")
