"""Shared plumbing for turning a `TimelineDoc` plus a real `Video`'s assets
into a resolved VSEL payload — download the assets the template declares,
probe their real duration, resolve the timeline, build the payload.

Split out of `timeline/preview.py` when `worker.py`'s production render path
started using the same steps (see `docs/edicao_declarativa.md`'s "corte
seco" decision, 08/09/2026). Both callers need this exact sequence; neither
needs the other's concerns:

- `worker.py`'s `_render` runs it already inside `worker.render_slot()` (the
  production concurrency gate acquired by its caller), then does a real
  render pass and uploads the result.
- `timeline/preview.py`'s `assemble_preview` wraps it with its own
  `preview_slot()` and stops at assembly — a preview is never rendered to a
  full MP4.

Neither the semaphore nor the Blender subprocess call lives here on purpose:
mixing worker.py's job into preview_slot() (or vice versa) would defeat the
whole reason the two gates are separate — an interactive preview must never
queue behind a ~12-minute production render, and a burst of production jobs
must never starve behind a preview limit sized for interactive use.

No `bpy` here — everything up to and including `build_payload` is pure
Python plus I/O (MinIO downloads, `ffprobe` subprocesses).
"""
from __future__ import annotations

import os

from src.blender_worker.db.models import Video
from src.blender_worker.storage.client import download_file
from src.blender_worker.timeline.payload import build_payload
from src.blender_worker.timeline.probe import duration_seconds_to_frames, probe_duration_seconds
from src.blender_worker.timeline.resolver import ResolvedTimeline, TimelineResolutionError, resolve_timeline
from src.blender_worker.timeline.schema import TimelineDoc

#: Which `Video` column backs each of the six standard input names. A
#: template declaring an input outside this set can't be resolved against a
#: real video — there is no way to know which asset it means.
INPUT_TO_VIDEO_FIELD = {
    "background": "video_file_key",
    "music": "music_key",
    "voice": "voice_key",
    "subtitles": "subtitle_key",
    "hook": "hook_voice_key",
    "card": "card_key",
}


async def download_and_probe_inputs(
    *, video: Video, doc: TimelineDoc, tmpdir: str, bucket: str, ffprobe_bin: str,
) -> tuple[dict[str, str], dict[str, int | None]]:
    """Downloads the assets `doc.inputs` declares that the video actually has
    (an optional input the video lacks is simply absent from both return
    dicts — `resolve_timeline` raises its usual "required input ... was not
    supplied" if that turns out to matter) and probes each one's length in
    frames via `ffprobe`.

    Returns `(asset_paths, inputs)`: `asset_paths` maps input name to the
    downloaded file's local path (what `apply_payload`'s clip handlers index
    by `clip["source"]`); `inputs` maps input name to a frame count, for
    `resolve_timeline`.
    """
    asset_paths: dict[str, str] = {}
    inputs: dict[str, int | None] = {}
    srt_inputs: list[str] = []

    for name, spec in doc.inputs.items():
        field = INPUT_TO_VIDEO_FIELD.get(name)
        if field is None:
            raise TimelineResolutionError(
                f"input {name!r} has no known asset source — only "
                f"{sorted(INPUT_TO_VIDEO_FIELD)} are supported"
            )
        key = getattr(video, field)
        if not key:
            inputs[name] = None
            continue

        path = os.path.join(tmpdir, f"{name}_{os.path.basename(key)}")
        await download_file(bucket, key, path)
        asset_paths[name] = path

        if spec.type == "image":
            inputs[name] = 0  # presence placeholder — a PNG has no duration
        elif spec.type == "srt":
            # ffprobe has nothing to read from a subtitle file — same "as
            # long as the voice" approximation resolver.py's own docstring
            # already documents for `duration: source` on a subtitles clip
            # (see "Known Fase 1 simplification"). Resolved in a second pass
            # below, once "voice" is known.
            srt_inputs.append(name)
        else:
            seconds = await probe_duration_seconds(path, ffprobe_bin=ffprobe_bin)
            inputs[name] = duration_seconds_to_frames(seconds, doc.canvas.fps)

    for name in srt_inputs:
        inputs[name] = inputs.get("voice")

    return asset_paths, inputs


async def resolve_video_timeline(
    *, video: Video, doc: TimelineDoc, flags: dict[str, bool], tmpdir: str, bucket: str, ffprobe_bin: str,
) -> tuple[dict[str, str], ResolvedTimeline, dict]:
    """`download_and_probe_inputs` plus `resolve_timeline`/`build_payload` —
    the full "real video in, render-ready payload out" sequence. Returns
    `(asset_paths, resolved, payload)`.
    """
    asset_paths, inputs = await download_and_probe_inputs(
        video=video, doc=doc, tmpdir=tmpdir, bucket=bucket, ffprobe_bin=ffprobe_bin,
    )
    resolved = resolve_timeline(doc, inputs=inputs, flags=flags)
    payload = build_payload(doc, resolved, flags=flags)
    return asset_paths, resolved, payload
