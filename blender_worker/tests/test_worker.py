import asyncio
import uuid
from pathlib import Path
from subprocess import CalledProcessError
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.blender_worker import worker
from src.blender_worker.db.engine import AsyncSessionLocal
from src.blender_worker.db.models import Job, JobStatus, Template, Video
from src.blender_worker.worker import render_job

DEFAULT_TEMPLATE_YAML = (
    Path(__file__).resolve().parents[1] / "templates_v2" / "default.yaml"
).read_bytes()


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


def _proc_ok():
    from subprocess import CompletedProcess
    return CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _render_mocks(**overrides):
    """The standard set of patches for a `_render` run that should reach the
    end successfully — every I/O boundary (MinIO, ffprobe, Blender, the DB
    session is real) mocked out. `resolve_video_timeline`'s own downloads and
    probing live in `timeline/assemble.py`, not `worker.py` — see that
    module's docstring for why the two are patched at different import
    paths."""
    defaults = dict(
        download_bytes=patch(
            "src.blender_worker.worker.download_bytes",
            new=AsyncMock(return_value=DEFAULT_TEMPLATE_YAML),
        ),
        worker_download_file=patch("src.blender_worker.worker.download_file", new_callable=AsyncMock),
        assemble_download_file=patch(
            "src.blender_worker.timeline.assemble.download_file", new_callable=AsyncMock
        ),
        assemble_probe=patch(
            "src.blender_worker.timeline.assemble.probe_duration_seconds",
            new_callable=AsyncMock, return_value=5.0,
        ),
        upload_file=patch("src.blender_worker.worker.upload_file", new_callable=AsyncMock),
        to_thread=patch(
            "src.blender_worker.worker.asyncio.to_thread", new_callable=AsyncMock,
            return_value=_proc_ok(),
        ),
        exists=patch("src.blender_worker.worker.os.path.exists", return_value=True),
    )
    defaults.update(overrides)
    return defaults


async def test_render_job_completes(session, video, template):
    job = await _make_job(session, video, template)
    mocks = _render_mocks()

    with (
        mocks["download_bytes"], mocks["worker_download_file"],
        mocks["assemble_download_file"], mocks["assemble_probe"],
        mocks["upload_file"], mocks["to_thread"], mocks["exists"],
    ):
        await render_job(job.id)

    updated = await _get_job(job.id)
    assert updated.status == JobStatus.completed
    assert updated.output_key == f"outputs/{job.id}/final.mp4"
    assert updated.blend_key == f"outputs/{job.id}/output.blend"
    assert updated.error is None


async def test_render_job_fails_without_a_yaml_key(session, video):
    # No v1 fallback — see docs/edicao_declarativa.md's "corte seco"
    # decision. A Template that never got a yaml_key must fail loudly, not
    # silently render the retired template.json-driven layout.
    t = Template(name="No YAML", blend_key="test/t.blend", json_key="test/t.json")
    session.add(t)
    await session.commit()
    await session.refresh(t)
    job = await _make_job(session, video, t)

    await render_job(job.id)

    updated = await _get_job(job.id)
    assert updated.status == JobStatus.failed
    assert "yaml_key" in updated.error


async def _run_and_capture_config(job_id, *, probe_seconds=5.0):
    """Run render_job with every side effect stubbed, returning the
    job_config that was about to be written to disk."""
    import src.blender_worker.worker as worker_module

    captured = {}
    real_dump = worker_module.json.dump

    def capture(payload, fh, *a, **kw):
        captured.update(payload)
        return real_dump(payload, fh, *a, **kw)

    mocks = _render_mocks(
        assemble_probe=patch(
            "src.blender_worker.timeline.assemble.probe_duration_seconds",
            new_callable=AsyncMock, return_value=probe_seconds,
        ),
    )
    downloaded_asset_keys = []

    async def _record_download(bucket, key, dest):
        downloaded_asset_keys.append(key)

    with (
        mocks["download_bytes"], mocks["worker_download_file"],
        patch("src.blender_worker.timeline.assemble.download_file", side_effect=_record_download),
        mocks["assemble_probe"], mocks["upload_file"], mocks["to_thread"], mocks["exists"],
        patch("src.blender_worker.worker.json.dump", side_effect=capture),
    ):
        await render_job(job_id)

    return captured, downloaded_asset_keys


async def test_render_job_without_intro_passes_only_the_four_base_assets(
    session, video, template
):
    job = await _make_job(session, video, template)

    config, downloaded = await _run_and_capture_config(job.id)

    assert set(config["assets"]) == {"background", "music", "voice", "subtitles"}
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


async def test_render_job_forwards_the_muted_hook_flag_into_the_payload(session, template):
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
    hook_clip = next(c for c in config["payload"]["clips"] if c["track"] == "hook")
    assert hook_clip["muted"] is True


async def test_render_job_defaults_to_an_audible_hook(session, video, template):
    job = await _make_job(session, video, template)

    config, _ = await _run_and_capture_config(job.id)

    voz_clip = next(c for c in config["payload"]["clips"] if c["track"] == "voz")
    assert voz_clip["muted"] is False


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


async def test_render_job_includes_the_cta_clip(session, video, template):
    job = await _make_job(session, video, template)

    config, _ = await _run_and_capture_config(job.id)

    cta = next(c for c in config["payload"]["clips"] if c["track"] == "cta")
    assert cta["type"] == "text"
    assert cta["text"] == "me ajude a pagar a faculdade, segue o perfil"


@pytest.fixture
def fresh_slot():
    """The gate is a module global built on first use — rebuild it per test."""
    worker._render_slot = None
    yield
    worker._render_slot = None


def _settings_with(**blender_keys):
    return SimpleNamespace(CONFIG=SimpleNamespace(blender=SimpleNamespace(**blender_keys)))


@pytest.mark.no_db
async def test_render_slot_runs_one_render_at_a_time(fresh_slot, monkeypatch):
    monkeypatch.setattr(worker, "settings", _settings_with(max_concurrent_renders=1))
    running = peak = 0

    async def slow_render(_job_id):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        running -= 1

    with patch("src.blender_worker.worker._render", new=slow_render):
        await asyncio.gather(*(render_job(uuid.uuid4()) for _ in range(4)))

    # Without the gate all four interleave at the first await and peak is 4.
    assert peak == 1


@pytest.mark.no_db
async def test_render_slot_honours_the_configured_limit(fresh_slot, monkeypatch):
    monkeypatch.setattr(worker, "settings", _settings_with(max_concurrent_renders=3))

    assert worker.render_slot()._value == 3


@pytest.mark.no_db
async def test_render_slot_falls_back_when_the_template_has_no_limit(fresh_slot, monkeypatch):
    # config.prod.ini, or any config predating the key, must not crash the render.
    monkeypatch.setattr(worker, "settings", _settings_with())

    assert worker.render_slot()._value == worker.DEFAULT_MAX_CONCURRENT_RENDERS


@pytest.mark.no_db
async def test_render_slot_never_goes_below_one(fresh_slot, monkeypatch):
    # A limit of 0 would deadlock every job forever, which is worse than the OOM.
    monkeypatch.setattr(worker, "settings", _settings_with(max_concurrent_renders=0))

    assert worker.render_slot()._value == 1


@pytest.mark.no_db
async def test_render_slot_is_released_when_a_render_raises(fresh_slot, monkeypatch):
    # `_render` swallows its own exceptions today; this guards the day it stops.
    monkeypatch.setattr(worker, "settings", _settings_with(max_concurrent_renders=1))

    async def boom(_job_id):
        raise RuntimeError("blender died")

    with patch("src.blender_worker.worker._render", new=boom):
        with pytest.raises(RuntimeError):
            await render_job(uuid.uuid4())

    assert not worker.render_slot().locked()


async def test_render_job_fails_on_subprocess_error(session, video, template):
    job = await _make_job(session, video, template)
    mocks = _render_mocks(
        to_thread=patch(
            "src.blender_worker.worker.asyncio.to_thread",
            side_effect=CalledProcessError(1, "blender"),
        ),
    )

    with (
        mocks["download_bytes"], mocks["worker_download_file"],
        mocks["assemble_download_file"], mocks["assemble_probe"],
        mocks["upload_file"], mocks["to_thread"],
    ):
        await render_job(job.id)

    updated = await _get_job(job.id)
    assert updated.status == JobStatus.failed
    assert updated.error is not None


async def test_render_job_fails_when_a_required_input_is_missing(session, template):
    # voice_key is required by the shipped template — resolve_timeline raises
    # before Blender is ever invoked.
    video = Video(
        video_file_key="test/video.mp4",
        music_key="test/music.mp3",
        voice_key="",
        subtitle_key="test/subs.srt",
    )
    session.add(video)
    await session.commit()
    job = await _make_job(session, video, template)

    mocks = _render_mocks()
    with (
        mocks["download_bytes"], mocks["worker_download_file"],
        mocks["assemble_download_file"], mocks["assemble_probe"],
        mocks["upload_file"], mocks["to_thread"], mocks["exists"],
    ):
        await render_job(job.id)

    updated = await _get_job(job.id)
    assert updated.status == JobStatus.failed
    assert "voice" in updated.error
