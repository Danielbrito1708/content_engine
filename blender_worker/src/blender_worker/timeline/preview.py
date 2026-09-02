"""Assembles a declarative-timeline preview (Fase 3, níveis 2/3 of
docs/edicao_declarativa.md) against a real `Video`'s assets.

Isolated from `worker.py` on purpose — this is a dev/operator tool, not the
production render path. `worker.py`, `render_job` and `render_slot()` stay
completely untouched; a preview waits on `preview_slot()`, a separate
semaphore, so an interactive "generate a preview" click never queues behind
a ~12-minute production render.

Splits the work the same way `worker.py`'s `_render` does — download assets,
write `job_config.json`, run Blender once to assemble (`scripts/edit_video.py`
via `-P`) — but stops there. Rendering the assembled `.blend` (a single frame
or a short clip) is the caller's job: it needs mode-specific CLI flags
(`-f N` vs `-s/-e -a`, an output format, an optional resolution override) that
have nothing to do with assembly and everything to do with which of the two
preview routes is calling.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile

from structlog import get_logger

from src.blender_worker.db.models import Video
from src.blender_worker.storage.client import download_file
from src.blender_worker.timeline.payload import build_payload
from src.blender_worker.timeline.probe import duration_seconds_to_frames, probe_duration_seconds
from src.blender_worker.timeline.resolver import ResolvedTimeline, TimelineResolutionError, resolve_timeline
from src.blender_worker.timeline.schema import TimelineDoc
from src.core import settings

log = get_logger(__name__)

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts")

#: Fallback for `config.ini [blender] max_concurrent_previews`.
DEFAULT_MAX_CONCURRENT_PREVIEWS = 1

_preview_slot: asyncio.Semaphore | None = None


def preview_slot() -> asyncio.Semaphore:
    """A gate independent of `worker.render_slot()` — see this module's
    docstring for why the two must never share state."""
    global _preview_slot
    if _preview_slot is None:
        limit = getattr(
            settings.CONFIG.blender,
            "max_concurrent_previews",
            DEFAULT_MAX_CONCURRENT_PREVIEWS,
        )
        _preview_slot = asyncio.Semaphore(max(1, int(limit)))
    return _preview_slot


#: Which `Video` column backs each of the six standard input names. A
#: template declaring an input outside this set can't be previewed against a
#: real video — there is no way to know which asset it means.
_INPUT_TO_VIDEO_FIELD = {
    "background": "video_file_key",
    "music": "music_key",
    "voice": "voice_key",
    "subtitles": "subtitle_key",
    "hook": "hook_voice_key",
    "card": "card_key",
}


async def assemble_preview(
    *, video: Video, template_blend_key: str, doc: TimelineDoc, flags: dict[str, bool],
) -> tuple[str, str, ResolvedTimeline]:
    """Downloads the real assets `doc.inputs` declares (skipping ones the
    video simply doesn't have — `resolve_timeline` raises its usual
    "required input ... was not supplied" if that turns out to matter),
    probes their real duration via `ffprobe`, resolves the timeline and
    assembles it in Blender (assembly only, no render).

    Returns `(output_blend_path, tmpdir, resolved)` on success — the caller
    owns `tmpdir` from here and must `shutil.rmtree` it once the actual
    preview render (frame or clip) is done. On any failure this cleans up
    after itself before re-raising; the caller never has to clean up here.
    """
    tmpdir = tempfile.mkdtemp(prefix="blender_worker_preview_")
    try:
        bucket = settings.CONFIG.storage.bucket
        ffprobe_bin = getattr(settings.CONFIG.blender, "ffprobe_bin", "ffprobe")

        asset_paths: dict[str, str] = {}
        inputs: dict[str, int | None] = {}
        srt_inputs: list[str] = []

        for name, spec in doc.inputs.items():
            field = _INPUT_TO_VIDEO_FIELD.get(name)
            if field is None:
                raise TimelineResolutionError(
                    f"input {name!r} has no known asset source for preview — only "
                    f"{sorted(_INPUT_TO_VIDEO_FIELD)} are supported"
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
                # ffprobe has nothing to read from a subtitle file — same
                # "as long as the voice" approximation resolver.py's own
                # docstring already documents for `duration: source` on a
                # subtitles clip (see "Known Fase 1 simplification").
                # Resolved in a second pass below, once "voice" is known.
                srt_inputs.append(name)
            else:
                seconds = await probe_duration_seconds(path, ffprobe_bin=ffprobe_bin)
                inputs[name] = duration_seconds_to_frames(seconds, doc.canvas.fps)

        for name in srt_inputs:
            inputs[name] = inputs.get("voice")

        resolved = resolve_timeline(doc, inputs=inputs, flags=flags)
        payload = build_payload(doc, resolved, flags=flags)

        template_blend = os.path.join(tmpdir, "template.blend")
        await download_file(bucket, template_blend_key, template_blend)

        output_path = os.path.join(tmpdir, "output.blend")
        config_path = os.path.join(tmpdir, "job_config.json")
        with open(config_path, "w") as f:
            json.dump({
                "assets": asset_paths,
                "payload": payload,
                "output_path": output_path,
                # Overwritten by the caller's `-o` at render time — assembly
                # needs some string here, never reads it back.
                "render_output_path": os.path.join(tmpdir, "render_output"),
            }, f)

        edit_script = os.path.join(SCRIPTS_DIR, "edit_video.py")
        blender_bin = settings.CONFIG.blender.bin

        async with preview_slot():
            result = await asyncio.to_thread(
                subprocess.run,
                [blender_bin, "-b", template_blend, "-P", edit_script, "--", config_path],
                capture_output=True,
                text=True,
            )
        if result.returncode != 0 or not os.path.exists(output_path):
            log.error(
                "preview assemble failed",
                returncode=result.returncode,
                stdout=result.stdout[-3000:],
                stderr=result.stderr[-3000:],
            )
            raise RuntimeError(f"Blender exited {result.returncode}. stderr: {result.stderr[-500:]}")

        return output_path, tmpdir, resolved
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
