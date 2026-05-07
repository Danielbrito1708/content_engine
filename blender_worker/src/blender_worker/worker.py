import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import uuid

from sqlalchemy import select
from structlog import get_logger

from src.blender_worker.db.engine import AsyncSessionLocal
from src.blender_worker.db.models import Job, JobStatus, Template, Video
from src.blender_worker.storage.client import download_file, upload_file
from src.core import settings

log = get_logger(__name__)

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")


async def render_job(job_id: uuid.UUID) -> None:
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

            bucket = settings.CONFIG.storage.bucket

            template_blend = os.path.join(tmpdir, "template.blend")
            template_json_path = os.path.join(tmpdir, "template.json")
            video_path = os.path.join(tmpdir, _filename(video.video_file_key))
            music_path = os.path.join(tmpdir, _filename(video.music_key))
            voice_path = os.path.join(tmpdir, _filename(video.voice_key))
            subtitle_path = os.path.join(tmpdir, _filename(video.subtitle_key))
            output_path = os.path.join(tmpdir, "output.blend")

            await download_file(bucket, template.blend_key, template_blend)
            await download_file(bucket, template.json_key, template_json_path)
            await download_file(bucket, video.video_file_key, video_path)
            await download_file(bucket, video.music_key, music_path)
            await download_file(bucket, video.voice_key, voice_path)
            await download_file(bucket, video.subtitle_key, subtitle_path)

            with open(template_json_path) as f:
                timing = json.load(f)

            rendered_file = os.path.join(tmpdir, "final.mp4")
            config_path = os.path.join(tmpdir, "job_config.json")
            with open(config_path, "w") as f:
                json.dump({
                    "job_id": str(job_id),
                    "output_path": output_path,
                    "render_output_path": rendered_file,
                    "assets": {
                        "video": video_path,
                        "music": music_path,
                        "voice": voice_path,
                        "subtitles": subtitle_path,
                    },
                    "timing": timing,
                }, f)

            edit_script = os.path.join(SCRIPTS_DIR, "edit_video.py")
            blender_bin = settings.CONFIG.blender.bin

            log.info("render started", job_id=str(job_id), blender=blender_bin)

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

            output_key = f"outputs/{job_id}.mp4"
            await upload_file(bucket, output_key, rendered_file)

            job.output_key = output_key
            job.status = JobStatus.completed
            log.info("render completed", job_id=str(job_id), output_key=output_key)

        except Exception as exc:
            job.status = JobStatus.failed
            job.error = str(exc)
            log.error("render failed", job_id=str(job_id), error=str(exc))

        finally:
            await session.commit()
            shutil.rmtree(tmpdir, ignore_errors=True)


def _filename(key: str) -> str:
    return os.path.basename(key)
