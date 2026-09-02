"""Serialises a `ResolvedTimeline` (see `resolver.py`) plus the `TimelineDoc`
it came from into the plain JSON dict that `scripts/edit_video.py`'s Fase 2
dispatcher (`apply_payload`) consumes. Pure — no `bpy`.

Fase 1's resolver deliberately only computes *time* (see its module
docstring). This module is what adds back the non-timing clip properties —
volume, style, y_position — that it left untouched on `ResolvedClip.clip`.
Keeping the two concerns apart means each stays separately testable: get the
frame numbers right without caring about pixels, then wire numbers to
pixels without re-deriving any of them.

The result is meant to be dropped straight into `job_config.json` next to
`assets` — every `source` field here is a *name*, matched against whatever
key `worker.py` used for that asset's downloaded path, same as
`assets["video"]` / `assets["hook"]` work today.

## Bed-clip geometry here is advisory, not authoritative

`repeats` (a `video` bed clip's extra loop copies) and `fade_start` (an
`audio` bed clip's `volume_fade`) are computed from `resolved.timeline_end`,
which Fase 1's resolver derives from `duration: source` on the `subtitles`
clip — i.e. "as long as the voice `input` says", not the SRT's real
word-by-word timing (see `resolver.py`'s "Known Fase 1 simplification").

A real-Blender verification run (`docs/edicao_declarativa.md`'s "Fase 2 —
executor") measured this gap directly: a synthetic SRT's last word, held
`max_hold_seconds` past its own timestamp, ended 10 frames after the voice
strip's own end — so the true content end was 10 frames later than this
module's `timeline_end`. Every single strip main() and apply_payload create
matched exactly except this one number.

The fix lives in `apply_payload`, not here: it creates every `content` clip
first, **re-derives `scene.frame_end` from the real strips** (the same
`content_end_frame` rule `main()` already uses), and only then creates `bed`
clips — recomputing their loop/fade geometry against that corrected value
instead of trusting `repeats`/`fade_start`. Those two fields still ship in
the payload because they are exactly right whenever the "as long as the
voice" approximation holds, and cheap to compute here for the millisecond
preview path (`docs/edicao_declarativa.md`'s "Loop de preview") — they are
just not what the final render uses.
"""
from __future__ import annotations

from src.blender_worker.timeline import expr
from src.blender_worker.timeline.resolver import ResolvedTimeline, TimelineResolutionError, resolve_flag_ref
from src.blender_worker.timeline.schema import (
    AudioClip,
    ImageClip,
    SubtitlesClip,
    TimelineDoc,
    VideoClip,
    VolumeFadeFilter,
)


def build_payload(doc: TimelineDoc, resolved: ResolvedTimeline, *, flags: dict[str, bool] | None = None) -> dict:
    flags = flags or {}
    frame_rate = doc.canvas.fps
    return {
        "frame_rate": frame_rate,
        "canvas": {"width": doc.canvas.width, "height": doc.canvas.height},
        # Advisory — see this module's docstring. apply_payload re-derives
        # the real end from the strips it creates; this is what the
        # millisecond preview path shows before any of that exists.
        "timeline_end": resolved.timeline_end,
        "tail_frames": _duration_frames(doc.canvas.tail, frame_rate),
        "clips": [_clip_payload_attributed(c, frame_rate, flags) for c in resolved.clips],
    }


def _clip_payload_attributed(c, frame_rate: int, flags: dict[str, bool]) -> dict:
    """Wraps `_clip_payload` so an error resolved only here — a bad
    `max_hold`/`fade`/`fade_out.duration` expression, or an unknown `$flag`
    on `AudioClip.muted` — names the offending track like `resolver.py`'s own
    errors already do. `resolve_timeline` never touches these fields (see
    this module's docstring), so this is the first point they're evaluated."""
    try:
        return _clip_payload(c, frame_rate, flags)
    except (expr.ExprError, TimelineResolutionError) as exc:
        raise type(exc)(f"track {c.track!r} ({c.type!r}): {exc}") from exc


def _source_name(clip) -> str | None:
    if not clip.source:
        return None
    return clip.source[1:] if clip.source.startswith("$") else clip.source


def _duration_frames(text: str, frame_rate: int) -> int:
    return expr.resolve(text, anchors={}, inputs={}, frame_rate=frame_rate)


def _clip_payload(c, frame_rate: int, flags: dict[str, bool]) -> dict:
    clip = c.clip
    payload = {
        "track": c.track,
        "channel": c.channel,
        "role": c.role,
        "type": c.type,
        "source": _source_name(clip),
        "frame_start": c.frame_start,
    }
    if c.frame_end is not None:
        payload["frame_end"] = c.frame_end
    if c.repeats:
        payload["repeats"] = c.repeats  # advisory — see module docstring

    if isinstance(clip, VideoClip):
        payload["min_frames"] = clip.min_frames
        payload["loop"] = clip.loop == "until_end"

    elif isinstance(clip, AudioClip):
        payload["volume"] = clip.volume
        payload["muted"] = resolve_flag_ref(clip.muted, flags)
        fade = next((f for f in clip.filters if isinstance(f, VolumeFadeFilter)), None)
        if fade is not None:
            # Recomputed live in apply_payload against the real
            # scene.frame_end — fade_start below is advisory (see module
            # docstring), fade_duration_frames/fade_to are what it uses.
            payload["fade_duration_frames"] = _duration_frames(fade.duration, frame_rate)
            payload["fade_to"] = fade.to
            if c.fade_start is not None:
                payload["fade_start"] = c.fade_start

    elif isinstance(clip, ImageClip):
        payload["y_position"] = clip.y_position
        fade_out = next((f for f in clip.filters if f.type == "fade_out"), None)
        duration = getattr(fade_out, "duration", None) if fade_out else None
        if duration is not None:
            payload["fade_frames"] = _duration_frames(duration, frame_rate)

    elif isinstance(clip, SubtitlesClip):
        payload["hide_before"] = c.extra.get("hide_before", 0)
        payload["max_hold_seconds"] = _duration_frames(clip.max_hold, frame_rate) / frame_rate
        payload["fade_frames"] = _duration_frames(clip.fade, frame_rate)
        if clip.rise:
            payload["rise_frames"] = clip.rise.get("frames", 0)
            payload["rise_offset"] = clip.rise.get("offset", 0.0)
        payload["style"] = _flatten_subtitle_style(clip.style)

    return payload


def _flatten_subtitle_style(style: dict) -> dict:
    """`style.outline.{color,width}` (this format) → `use_outline` /
    `outline_color` / `outline_width` (the flat shape
    `edit_video.resolve_subtitle_style` already reads from the legacy
    `template.json`). The new format nests because it reads better; the
    legacy function is left alone because it works and has its own tests."""
    flat = {k: v for k, v in style.items() if k != "outline"}
    outline = style.get("outline") or {}
    if outline:
        flat["use_outline"] = True
        if "color" in outline:
            flat["outline_color"] = outline["color"]
        if "width" in outline:
            flat["outline_width"] = outline["width"]
    return flat
