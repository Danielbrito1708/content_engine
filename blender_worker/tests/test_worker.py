import uuid
from subprocess import CalledProcessError
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from src.blender_worker.db.engine import AsyncSessionLocal
from src.blender_worker.db.models import Job, JobStatus, Template, Video
from src.blender_worker.worker import render_job


async def _get_job(job_id):
    async with AsyncSessionLocal() as s:
        return await s.get(Job, job_id)


async def _make_job(session, video, template):
    job = Job(video_id=video.id, template_id=template.id)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def test_render_job_not_found_does_not_raise():
    await render_job(uuid.uuid4())


async def test_render_job_completes(session, video, template):
    from subprocess import CompletedProcess
    from unittest.mock import MagicMock

    job = await _make_job(session, video, template)

    proc_ok = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch("src.blender_worker.worker.download_file", new_callable=AsyncMock),
        patch("src.blender_worker.worker.upload_file", new_callable=AsyncMock),
        patch("src.blender_worker.worker.asyncio.to_thread", new_callable=AsyncMock,
              return_value=proc_ok),
        patch("src.blender_worker.worker.json.load", return_value={
            "frame_rate": 30, "frame_end": 900,
            "channels": {"video": 1, "music": 2, "voice": 3, "subtitles": 4},
            "timing": {"intro_start": 0, "speech_start": 90},
        }),
        patch("builtins.open", create=True),
        patch("src.blender_worker.worker.os.path.exists", return_value=True),
    ):
        await render_job(job.id)

    updated = await _get_job(job.id)
    assert updated.status == JobStatus.completed
    assert updated.output_key == f"outputs/{job.id}/final.mp4"
    assert updated.blend_key == f"outputs/{job.id}/output.blend"
    assert updated.error is None


async def test_render_job_fails_on_subprocess_error(session, video, template):
    job = await _make_job(session, video, template)

    with (
        patch("src.blender_worker.worker.download_file", new_callable=AsyncMock),
        patch("src.blender_worker.worker.upload_file", new_callable=AsyncMock),
        patch("src.blender_worker.worker.asyncio.to_thread",
              side_effect=CalledProcessError(1, "blender")),
        patch("src.blender_worker.worker.json.load", return_value={
            "frame_rate": 30, "frame_end": 900,
            "channels": {}, "timing": {},
        }),
        patch("builtins.open", create=True),
    ):
        await render_job(job.id)

    updated = await _get_job(job.id)
    assert updated.status == JobStatus.failed
    assert updated.error is not None
