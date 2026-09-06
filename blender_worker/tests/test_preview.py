"""Tests for `timeline/preview.py` — `assemble_preview` and `preview_slot()`.
Everything that touches the outside world (MinIO, ffprobe, Blender) is
mocked; only real filesystem temp dirs are used, so cleanup behaviour is
exercised for real.

Pure w.r.t. bpy/DB/MinIO — marked `no_db`.
"""
import asyncio
import json
import os
import shutil
import tempfile
import uuid
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.blender_worker.timeline import preview
from src.blender_worker.timeline.preview import assemble_preview, preview_slot
from src.blender_worker.timeline.resolver import TimelineResolutionError
from src.blender_worker.timeline.schema import TimelineDoc

pytestmark = pytest.mark.no_db


def _video(**overrides):
    base = dict(
        video_file_key="bg.mp4", music_key="music.mp3", voice_key="voice.mp3",
        subtitle_key="subs.srt", hook_voice_key=None, card_key=None,
    )
    base.update(overrides)
    return SimpleNamespace(id=uuid.uuid4(), **base)


def _settings_with(**blender_keys):
    return SimpleNamespace(
        CONFIG=SimpleNamespace(
            storage=SimpleNamespace(bucket="test-bucket"),
            blender=SimpleNamespace(bin="/usr/local/bin/blender", **blender_keys),
        )
    )


_MINIMAL_DOC = {
    "version": 2,
    "canvas": {"width": 1080, "height": 1920, "fps": 30, "fallback_end": 900},
    "inputs": {
        "background": {"type": "video", "required": True},
        "music": {"type": "audio", "required": True},
        "voice": {"type": "audio", "required": True},
        "subtitles": {"type": "srt", "required": True},
        "hook": {"type": "audio", "required": False},
        "card": {"type": "image", "required": False},
    },
    "tracks": [
        {"name": "fundo", "channel": 1, "role": "bed", "clips": [
            {"type": "video", "source": "$background", "start": "0s", "loop": "until_end"},
        ]},
        {"name": "voz", "channel": 3, "role": "content", "clips": [
            {"type": "audio", "source": "$voice", "start": "0s"},
        ]},
    ],
}


def _doc(**overrides):
    raw = json.loads(json.dumps(_MINIMAL_DOC))
    raw.update(overrides)
    return TimelineDoc.model_validate(raw)


@pytest.fixture
def fresh_preview_slot():
    preview._preview_slot = None
    yield
    preview._preview_slot = None


@pytest.fixture
def real_tmpdir(monkeypatch):
    """Points assemble_preview at a real, test-owned temp dir so we can
    assert it does/doesn't survive, and so the Blender-mock side effect can
    drop a file at a known path."""
    d = tempfile.mkdtemp(prefix="test_assemble_preview_")
    monkeypatch.setattr(preview.tempfile, "mkdtemp", lambda prefix=None: d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


async def test_unknown_input_name_raises_and_cleans_up_tmpdir(monkeypatch, real_tmpdir):
    monkeypatch.setattr(preview, "settings", _settings_with())
    doc = _doc(inputs={"mystery": {"type": "video", "required": True}}, tracks=[])

    with pytest.raises(TimelineResolutionError, match="mystery"):
        await assemble_preview(video=_video(), template_blend_key="t.blend", doc=doc, flags={})

    assert not os.path.exists(real_tmpdir)


async def test_missing_required_input_propagates_resolver_error(monkeypatch, real_tmpdir):
    monkeypatch.setattr(preview, "settings", _settings_with())
    video = _video(voice_key=None)

    with (
        patch("src.blender_worker.timeline.preview.download_file", new_callable=AsyncMock),
        patch("src.blender_worker.timeline.preview.probe_duration_seconds", new_callable=AsyncMock),
    ):
        with pytest.raises(TimelineResolutionError, match="voice"):
            await assemble_preview(video=video, template_blend_key="t.blend", doc=_doc(), flags={})

    assert not os.path.exists(real_tmpdir)


async def test_missing_optional_asset_is_skipped_without_downloading(monkeypatch, real_tmpdir):
    monkeypatch.setattr(preview, "settings", _settings_with())
    video = _video()  # hook_voice_key/card_key already None

    proc_ok = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def fake_run(*_a, **_k):
        open(os.path.join(real_tmpdir, "output.blend"), "w").close()
        return proc_ok

    with (
        patch("src.blender_worker.timeline.preview.download_file", new_callable=AsyncMock) as download,
        patch("src.blender_worker.timeline.preview.probe_duration_seconds",
              new_callable=AsyncMock, return_value=10.0),
        patch("src.blender_worker.timeline.preview.subprocess.run", side_effect=fake_run),
    ):
        output_path, tmpdir, resolved = await assemble_preview(
            video=video, template_blend_key="t.blend", doc=_doc(), flags={}
        )

    downloaded_keys = [call.args[1] for call in download.await_args_list]
    assert "hook" not in " ".join(downloaded_keys)
    assert output_path == os.path.join(real_tmpdir, "output.blend")
    assert tmpdir == real_tmpdir
    assert resolved.timeline_end > 0


async def test_image_input_is_not_probed(monkeypatch, real_tmpdir):
    monkeypatch.setattr(preview, "settings", _settings_with())
    video = _video(card_key="card.png")
    doc = _doc(tracks=_MINIMAL_DOC["tracks"] + [
        {"name": "card", "channel": 6, "role": "content", "when_present": "$card", "clips": [
            {"type": "image", "source": "$card", "start": "0s", "until": "10f"},
        ]},
    ])

    proc_ok = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def fake_run(*_a, **_k):
        open(os.path.join(real_tmpdir, "output.blend"), "w").close()
        return proc_ok

    with (
        patch("src.blender_worker.timeline.preview.download_file", new_callable=AsyncMock),
        patch("src.blender_worker.timeline.preview.probe_duration_seconds",
              new_callable=AsyncMock, return_value=10.0) as probe,
        patch("src.blender_worker.timeline.preview.subprocess.run", side_effect=fake_run),
    ):
        await assemble_preview(video=video, template_blend_key="t.blend", doc=doc, flags={})

    probed_paths = [call.args[0] for call in probe.await_args_list]
    assert not any("card" in p for p in probed_paths)


async def test_subtitles_duration_mirrors_voice_instead_of_being_probed(monkeypatch, real_tmpdir):
    """ffprobe has nothing to read from a `.srt` — a real one made this fail
    in production (`ffprobe produced no duration for '...part_1.srt'`) before
    this fix. Subtitles duration must mirror voice's, same approximation
    resolver.py already documents for `duration: source`."""
    monkeypatch.setattr(preview, "settings", _settings_with())
    proc_ok = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def fake_run(*_a, **_k):
        open(os.path.join(real_tmpdir, "output.blend"), "w").close()
        return proc_ok

    with (
        patch("src.blender_worker.timeline.preview.download_file", new_callable=AsyncMock),
        patch("src.blender_worker.timeline.preview.probe_duration_seconds",
              new_callable=AsyncMock, return_value=12.0) as probe,
        patch("src.blender_worker.timeline.preview.subprocess.run", side_effect=fake_run),
    ):
        await assemble_preview(video=_video(), template_blend_key="t.blend", doc=_doc(), flags={})

    probed_paths = [call.args[0] for call in probe.await_args_list]
    assert not any("subtitles" in p for p in probed_paths)
    assert os.path.join(real_tmpdir, "voice_voice.mp3") in probed_paths


async def test_successful_assemble_writes_payload_config(monkeypatch, real_tmpdir):
    monkeypatch.setattr(preview, "settings", _settings_with())
    video = _video()
    proc_ok = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def fake_run(*_a, **_k):
        open(os.path.join(real_tmpdir, "output.blend"), "w").close()
        return proc_ok

    with (
        patch("src.blender_worker.timeline.preview.download_file", new_callable=AsyncMock),
        patch("src.blender_worker.timeline.preview.probe_duration_seconds",
              new_callable=AsyncMock, return_value=5.0),
        patch("src.blender_worker.timeline.preview.subprocess.run", side_effect=fake_run),
    ):
        await assemble_preview(video=video, template_blend_key="t.blend", doc=_doc(), flags={})

    with open(os.path.join(real_tmpdir, "job_config.json")) as f:
        config = json.load(f)
    assert "payload" in config
    assert "timing" not in config
    assert set(config["assets"]) == {"background", "music", "voice", "subtitles"}


async def test_blender_assemble_failure_cleans_up_and_raises(monkeypatch, real_tmpdir):
    monkeypatch.setattr(preview, "settings", _settings_with())
    proc_fail = CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")

    with (
        patch("src.blender_worker.timeline.preview.download_file", new_callable=AsyncMock),
        patch("src.blender_worker.timeline.preview.probe_duration_seconds",
              new_callable=AsyncMock, return_value=5.0),
        patch("src.blender_worker.timeline.preview.subprocess.run", return_value=proc_fail),
    ):
        with pytest.raises(RuntimeError, match="Blender exited 1"):
            await assemble_preview(video=_video(), template_blend_key="t.blend", doc=_doc(), flags={})

    assert not os.path.exists(real_tmpdir)


# --- preview_slot() — mirrors test_worker.py's render_slot() tests -------


async def test_preview_slot_runs_one_at_a_time(fresh_preview_slot, monkeypatch):
    monkeypatch.setattr(preview, "settings", _settings_with(max_concurrent_previews=1))
    running = peak = 0

    async def hold():
        nonlocal running, peak
        async with preview_slot():
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0)
            running -= 1

    await asyncio.gather(*(hold() for _ in range(4)))
    assert peak == 1


async def test_preview_slot_is_independent_of_render_slot(fresh_preview_slot, monkeypatch):
    from src.blender_worker import worker

    monkeypatch.setattr(preview, "settings", _settings_with(max_concurrent_previews=2))
    monkeypatch.setattr(worker, "settings", _settings_with(max_concurrent_renders=1))
    worker._render_slot = None

    assert preview_slot()._value == 2
    assert worker.render_slot()._value == 1
    worker._render_slot = None


async def test_preview_slot_falls_back_when_unconfigured(fresh_preview_slot, monkeypatch):
    monkeypatch.setattr(preview, "settings", _settings_with())
    assert preview_slot()._value == preview.DEFAULT_MAX_CONCURRENT_PREVIEWS
