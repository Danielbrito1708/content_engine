"""Tests for `POST /timelines/preview/frame` and `POST /timelines/preview/clip`
— Fase 3, níveis 2/3 of docs/edicao_declarativa.md.

Unlike `/validate` (nível 1), these need real `Video`/`Template` rows (the
routes 404 on them), so they run against the DB like the rest of the API
tests (`docker compose up db` required — not `no_db`). `assemble_preview`
and the pass-2 Blender subprocess are mocked; no real Blender or ffprobe.
"""
import os
import shutil
import tempfile
import uuid
from subprocess import CompletedProcess
from unittest.mock import AsyncMock, patch

import pytest

from src.blender_worker.timeline.resolver import ResolvedTimeline, TimelineResolutionError

TEMPLATE_TEXT = """
version: 2
canvas: {width: 1080, height: 1920, fps: 30, fallback_end: 900}
inputs:
  background: {type: video, required: true}
tracks:
  - name: fundo
    channel: 1
    role: bed
    clips:
      - {type: video, source: $background, start: 0s, loop: until_end}
"""


@pytest.fixture
def assembled_dir():
    d = tempfile.mkdtemp(prefix="test_preview_route_")
    output_path = os.path.join(d, "output.blend")
    open(output_path, "w").close()
    yield d, output_path
    shutil.rmtree(d, ignore_errors=True)


def _resolved():
    return ResolvedTimeline(timeline_end=901, anchors={}, clips=[])


PATCH_ASSEMBLE = "src.blender_worker.api.routes.timelines.assemble_preview"
PATCH_RUN = "src.blender_worker.api.routes.timelines.subprocess.run"


async def test_frame_preview_404s_on_unknown_video(client, template):
    resp = await client.post("/timelines/preview/frame", json={
        "template": TEMPLATE_TEXT, "video_id": str(uuid.uuid4()),
        "template_id": str(template.id), "frame": 1,
    })
    assert resp.status_code == 404


async def test_frame_preview_404s_on_unknown_template(client, video):
    resp = await client.post("/timelines/preview/frame", json={
        "template": TEMPLATE_TEXT, "video_id": str(video.id),
        "template_id": str(uuid.uuid4()), "frame": 1,
    })
    assert resp.status_code == 404


async def test_frame_preview_422s_on_malformed_template(client, video, template):
    resp = await client.post("/timelines/preview/frame", json={
        "template": "tracks: [{", "video_id": str(video.id),
        "template_id": str(template.id), "frame": 1,
    })
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["location"] == "<yaml>"


async def test_frame_preview_422s_when_assemble_fails_to_resolve(client, video, template):
    with patch(PATCH_ASSEMBLE, new_callable=AsyncMock,
               side_effect=TimelineResolutionError("required input 'background' was not supplied")):
        resp = await client.post("/timelines/preview/frame", json={
            "template": TEMPLATE_TEXT, "video_id": str(video.id),
            "template_id": str(template.id), "frame": 1,
        })
    assert resp.status_code == 422
    assert "background" in resp.json()["detail"][0]["message"]


async def test_frame_preview_502s_when_blender_render_fails(client, video, template, assembled_dir):
    d, output_path = assembled_dir
    proc_fail = CompletedProcess(args=[], returncode=1, stdout="", stderr="segfault")

    with (
        patch(PATCH_ASSEMBLE, new_callable=AsyncMock, return_value=(output_path, d, _resolved())),
        patch(PATCH_RUN, return_value=proc_fail),
    ):
        resp = await client.post("/timelines/preview/frame", json={
            "template": TEMPLATE_TEXT, "video_id": str(video.id),
            "template_id": str(template.id), "frame": 1,
        })
    assert resp.status_code == 502
    assert not os.path.exists(d)  # cleaned up even on failure


async def test_frame_preview_returns_the_rendered_png(client, video, template, assembled_dir):
    d, output_path = assembled_dir
    png_bytes = b"\x89PNG\r\n\x1a\nfake"

    def fake_run(cmd, **_kw):
        prefix = cmd[cmd.index("-o") + 1]
        with open(prefix + "0001.png", "wb") as f:
            f.write(png_bytes)
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch(PATCH_ASSEMBLE, new_callable=AsyncMock, return_value=(output_path, d, _resolved())),
        patch(PATCH_RUN, side_effect=fake_run),
    ):
        resp = await client.post("/timelines/preview/frame", json={
            "template": TEMPLATE_TEXT, "video_id": str(video.id),
            "template_id": str(template.id), "frame": 250,
        })

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == png_bytes
    assert not os.path.exists(d)


async def test_clip_preview_returns_the_rendered_mp4(client, video, template, assembled_dir):
    d, output_path = assembled_dir
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42fake"

    def fake_run(cmd, **_kw):
        out = cmd[cmd.index("-o") + 1]
        with open(out, "wb") as f:
            f.write(mp4_bytes)
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch(PATCH_ASSEMBLE, new_callable=AsyncMock, return_value=(output_path, d, _resolved())),
        patch(PATCH_RUN, side_effect=fake_run),
    ):
        resp = await client.post("/timelines/preview/clip", json={
            "template": TEMPLATE_TEXT, "video_id": str(video.id),
            "template_id": str(template.id), "start_s": 0, "duration_s": 5,
        })

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == mp4_bytes


async def test_clip_preview_422s_when_range_is_empty_after_clamping(client, video, template, assembled_dir):
    d, output_path = assembled_dir
    resolved = ResolvedTimeline(timeline_end=1, anchors={}, clips=[])  # timeline_end < start_frame

    with patch(PATCH_ASSEMBLE, new_callable=AsyncMock, return_value=(output_path, d, resolved)):
        resp = await client.post("/timelines/preview/clip", json={
            "template": TEMPLATE_TEXT, "video_id": str(video.id),
            "template_id": str(template.id), "start_s": 10, "duration_s": 5,
        })

    assert resp.status_code == 422
    assert not os.path.exists(d)


async def test_clip_preview_passes_resolution_percentage_before_render_flags(
    client, video, template, assembled_dir
):
    d, output_path = assembled_dir
    captured = {}

    def fake_run(cmd, **_kw):
        captured["cmd"] = cmd
        out = cmd[cmd.index("-o") + 1]
        with open(out, "wb") as f:
            f.write(b"fake")
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch(PATCH_ASSEMBLE, new_callable=AsyncMock, return_value=(output_path, d, _resolved())),
        patch(PATCH_RUN, side_effect=fake_run),
    ):
        resp = await client.post("/timelines/preview/clip", json={
            "template": TEMPLATE_TEXT, "video_id": str(video.id),
            "template_id": str(template.id), "start_s": 0, "duration_s": 5,
            "resolution_percentage": 50,
        })

    assert resp.status_code == 200
    cmd = captured["cmd"]
    assert "--python-expr" in cmd
    expr_index = cmd.index("--python-expr")
    assert "resolution_percentage = 50" in cmd[expr_index + 1]
    assert expr_index < cmd.index("-o")
    assert expr_index < cmd.index("-a")
