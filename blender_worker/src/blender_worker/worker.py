import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import uuid

from structlog import get_logger

from src.blender_worker.db.engine import AsyncSessionLocal
from src.blender_worker.db.models import Job, JobStatus, Template, Video
from src.blender_worker.storage.client import download_bytes, download_file, upload_file
from src.blender_worker.timeline.assemble import resolve_video_timeline
from src.blender_worker.timeline.loader import load_timeline
from src.core import settings

log = get_logger(__name__)

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")

#: Fallback for `config.ini [blender] max_concurrent_renders`.
DEFAULT_MAX_CONCURRENT_RENDERS = 1

_render_slot: asyncio.Semaphore | None = None


def render_slot() -> asyncio.Semaphore:
    """The concurrency gate, built on first use so it reads config after bootstrap."""
    global _render_slot
    if _render_slot is None:
        limit = getattr(
            settings.CONFIG.blender,
            "max_concurrent_renders",
            DEFAULT_MAX_CONCURRENT_RENDERS,
        )
        _render_slot = asyncio.Semaphore(max(1, int(limit)))
    return _render_slot


async def render_job(job_id: uuid.UUID) -> None:
    """Wait for a render slot, then render. Queued jobs stay `pending`."""
    slot = render_slot()
    if slot.locked():
        log.info("render queued", job_id=str(job_id))
    async with slot:
        await _render(job_id)


async def _render(job_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None:
            log.error("job not found", job_id=str(job_id))
            return

        job.status = JobStatus.running
        await session.commit()

        tmpdir = tempfile.mkdtemp(prefix=f"blender_worker_{job_id}_")
        try:
            video = await session.get(Video, job.video_id)
            template = await session.get(Template, job.template_id)

            if not template.yaml_key:
                # No v1 fallback, deliberately — see docs/edicao_declarativa.md's
                # "corte seco" decision (08/09/2026). The legacy template.json
                # -driven main() still exists in edit_video.py but nothing here
                # calls it any more; a Template that never got a yaml_key is a
                # setup problem, not a degradable one, so this fails the job
                # loudly instead of silently rendering the old layout.
                raise RuntimeError(
                    f"Template {template.id} has no yaml_key — the "
                    f"template.json-driven render path was retired. Publish a "
                    f"VSEL template.yaml to the bucket and PATCH "
                    f"/templates/{template.id} with its key before rendering "
                    f"(see blender_worker/CLAUDE.md, VSEL section)."
                )

            bucket = settings.CONFIG.storage.bucket
            ffprobe_bin = getattr(settings.CONFIG.blender, "ffprobe_bin", "ffprobe")

            yaml_bytes = await download_bytes(bucket, template.yaml_key)
            doc = load_timeline(yaml_bytes.decode("utf-8"))
            flags = {"hook_muted": bool(video.hook_muted)}

            asset_paths, resolved, payload = await resolve_video_timeline(
                video=video, doc=doc, flags=flags, tmpdir=tmpdir, bucket=bucket, ffprobe_bin=ffprobe_bin,
            )

            template_blend = os.path.join(tmpdir, "template.blend")
            await download_file(bucket, template.blend_key, template_blend)

            output_path = os.path.join(tmpdir, "output.blend")
            rendered_file = os.path.join(tmpdir, "final.mp4")
            config_path = os.path.join(tmpdir, "job_config.json")
            with open(config_path, "w") as f:
                json.dump({
                    "job_id": str(job_id),
                    "output_path": output_path,
                    "render_output_path": rendered_file,
                    "assets": asset_paths,
                    "payload": payload,
                }, f)

            edit_script = os.path.join(SCRIPTS_DIR, "edit_video.py")
            blender_bin = settings.CONFIG.blender.bin

            log.info(
                "render started", job_id=str(job_id), blender=blender_bin,
                timeline_end=resolved.timeline_end,
            )

            result = await asyncio.to_thread(
                subprocess.run,
                [blender_bin, "-b", template_blend, "-P", edit_script, "--", config_path],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or not os.path.exists(output_path):
                log.error(
                    "blender failed",
                    job_id=str(job_id),
                    returncode=result.returncode,
                    stdout=result.stdout[-3000:],
                    stderr=result.stderr[-3000:],
                )
                raise RuntimeError(
                    f"Blender exited {result.returncode}. stderr: {result.stderr[-500:]}"
                )

            # Render assembled .blend to MP4 (filepath set inside the .blend by edit_video.py)
            render_result = await asyncio.to_thread(
                subprocess.run,
                [blender_bin, "-b", output_path, "-a"],
                capture_output=True,
                text=True,
            )
            if render_result.returncode != 0 or not os.path.exists(rendered_file):
                log.error(
                    "render failed",
                    job_id=str(job_id),
                    returncode=render_result.returncode,
                    stdout=render_result.stdout[-3000:],
                    stderr=render_result.stderr[-3000:],
                )
                raise RuntimeError(
                    f"Render exited {render_result.returncode}. stderr: {render_result.stderr[-500:]}"
                )

            output_key = f"outputs/{job_id}/final.mp4"
            await upload_file(bucket, output_key, rendered_file)

            blend_key = f"outputs/{job_id}/output.blend"
            await upload_file(bucket, blend_key, output_path)

            job.output_key = output_key
            job.blend_key = blend_key
            job.status = JobStatus.completed
            log.info("render completed", job_id=str(job_id), output_key=output_key, blend_key=blend_key)

        except Exception as exc:
            job.status = JobStatus.failed
            job.error = str(exc)
            log.error("render failed", job_id=str(job_id), error=str(exc))

        finally:
            await session.commit()
            shutil.rmtree(tmpdir, ignore_errors=True)
