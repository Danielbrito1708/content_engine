"""Assembles a declarative-timeline preview (Fase 3, níveis 2/3 of
docs/edicao_declarativa.md) against a real `Video`'s assets.

This is a dev/operator tool, not the production render path — but since
08/09/2026 the production path (`worker.py`'s `_render`) resolves the same
kind of VSEL timeline for real jobs, so the two now share the
download/probe/resolve/build-payload sequence (`timeline/assemble.py`). What
stays isolated on purpose is the concurrency gate and the Blender subprocess
call: a preview waits on its own `preview_slot()`, never `worker.render_slot()`,
so an interactive "generate a preview" click never queues behind a
~12-minute production render — and, symmetrically, a burst of production
jobs never starves behind a limit sized for interactive use.

Splits the work the same way `worker.py`'s `_render` does — download assets,
write `job_config.json`, run Blender once to assemble (`scripts/edit_video.py`
via `-P`) — but stops there. Rendering the assembled `.blend` (a single frame
or a short clip) is the caller's job: it needs mode-specific CLI flags
(`-f N` vs `-s/-e -a`, an output format, an optional resolution override) that
have nothing to do with assembly and everything to do with which of the two
preview routes is calling.

The download/probe/resolve/build-payload sequence itself now lives in
`timeline/assemble.py`, shared with `worker.py`'s production path — see that
module's docstring for why the two callers still each keep their own
semaphore and their own Blender subprocess call.
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
from src.blender_worker.timeline.assemble import resolve_video_timeline
from src.blender_worker.timeline.resolver import ResolvedTimeline
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

        asset_paths, resolved, payload = await resolve_video_timeline(
            video=video, doc=doc, flags=flags, tmpdir=tmpdir, bucket=bucket, ffprobe_bin=ffprobe_bin,
        )

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
