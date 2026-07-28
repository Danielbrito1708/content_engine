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


async def _run_and_capture_config(job_id):
    """Run render_job with every side effect stubbed, returning the job_config."""
    from subprocess import CompletedProcess

    captured = {}

    def capture(payload, _fh):
        captured.update(payload)

    proc_ok = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch("src.blender_worker.worker.download_file", new_callable=AsyncMock) as download,
        patch("src.blender_worker.worker.upload_file", new_callable=AsyncMock),
        patch("src.blender_worker.worker.asyncio.to_thread", new_callable=AsyncMock,
              return_value=proc_ok),
        patch("src.blender_worker.worker.json.load", return_value={
            "frame_rate": 30, "frame_end": 900,
            "channels": {"video": 1, "music": 2, "voice": 3, "subtitles": 4},
            "timing": {"intro_start": 0, "speech_start": 90},
        }),
        patch("src.blender_worker.worker.json.dump", side_effect=capture),
        patch("builtins.open", create=True),
        patch("src.blender_worker.worker.os.path.exists", return_value=True),
    ):
        await render_job(job_id)

    downloaded = [call.args[1] for call in download.await_args_list]
    return captured, downloaded


async def test_render_job_without_intro_passes_only_the_four_base_assets(
    session, video, template
):
    job = await _make_job(session, video, template)

    config, downloaded = await _run_and_capture_config(job.id)

    assert set(config["assets"]) == {"video", "music", "voice", "subtitles"}
    assert not [key for key in downloaded if "card" in key or "hook" in key]


async def test_render_job_downloads_and_passes_the_intro_assets(session, template):
    video = Video(
        video_file_key="test/video.mp4",
        music_key="test/music.mp3",
        voice_key="test/voice.mp3",
        subtitle_key="test/subs.srt",
        card_key="cards/run.png",
        hook_voice_key="audio/run/hook.mp3",
    )
    session.add(video)
    await session.commit()
    job = await _make_job(session, video, template)

    config, downloaded = await _run_and_capture_config(job.id)

    assert config["assets"]["card"].endswith("run.png")
    assert config["assets"]["hook"].endswith("hook.mp3")
    assert "cards/run.png" in downloaded
    assert "audio/run/hook.mp3" in downloaded


async def test_render_job_forwards_the_muted_hook_flag(session, template):
    video = Video(
        video_file_key="test/video.mp4",
        music_key="test/music.mp3",
        voice_key="test/voice.mp3",
        subtitle_key="test/subs.srt",
        card_key="cards/run.png",
        hook_voice_key="audio/run/hook.mp3",
        hook_muted=True,
    )
    session.add(video)
    await session.commit()
    job = await _make_job(session, video, template)

    config, downloaded = await _run_and_capture_config(job.id)

    # Still downloaded: muted or not, the file is what the card is measured by.
    assert "audio/run/hook.mp3" in downloaded
    assert config["hook_muted"] is True


async def test_render_job_defaults_to_an_audible_hook(session, video, template):
    job = await _make_job(session, video, template)

    config, _ = await _run_and_capture_config(job.id)

    assert config["hook_muted"] is False


async def test_render_job_takes_the_card_without_the_hook(session, template):
    # The two are independent: the hook TTS is degradable upstream, so a video
    # can carry a card and no narration for it.
    video = Video(
        video_file_key="test/video.mp4",
        music_key="test/music.mp3",
        voice_key="test/voice.mp3",
        subtitle_key="test/subs.srt",
        card_key="cards/run.png",
    )
    session.add(video)
    await session.commit()
    job = await _make_job(session, video, template)

    config, _ = await _run_and_capture_config(job.id)

    assert "card" in config["assets"]
    assert "hook" not in config["assets"]


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
