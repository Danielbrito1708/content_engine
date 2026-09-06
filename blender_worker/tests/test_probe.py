"""Tests for `timeline/probe.py` — real asset duration via `ffprobe`,
subprocess mocked throughout. Pure — no bpy, no DB, no MinIO, no real ffprobe.
"""
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from src.blender_worker.timeline.probe import (
    ProbeError,
    duration_seconds_to_frames,
    probe_duration_seconds,
)

pytestmark = pytest.mark.no_db


async def test_probe_duration_seconds_parses_stdout():
    proc = CompletedProcess(args=[], returncode=0, stdout="12.345000\n", stderr="")
    with patch("src.blender_worker.timeline.probe.subprocess.run", return_value=proc) as run:
        seconds = await probe_duration_seconds("/tmp/voice.mp3", ffprobe_bin="ffprobe")

    assert seconds == pytest.approx(12.345)
    args = run.call_args.args[0]
    assert args[0] == "ffprobe"
    assert args[-1] == "/tmp/voice.mp3"


async def test_probe_duration_seconds_raises_on_nonzero_exit():
    proc = CompletedProcess(args=[], returncode=1, stdout="", stderr="No such file")
    with patch("src.blender_worker.timeline.probe.subprocess.run", return_value=proc):
        with pytest.raises(ProbeError, match="No such file"):
            await probe_duration_seconds("/tmp/missing.mp3")


async def test_probe_duration_seconds_raises_on_unparseable_stdout():
    proc = CompletedProcess(args=[], returncode=0, stdout="N/A\n", stderr="")
    with patch("src.blender_worker.timeline.probe.subprocess.run", return_value=proc):
        with pytest.raises(ProbeError, match="no duration"):
            await probe_duration_seconds("/tmp/weird.bin")


def test_duration_seconds_to_frames_rounds():
    assert duration_seconds_to_frames(2.6, 30) == 78
    assert duration_seconds_to_frames(0.0, 30) == 0
    assert duration_seconds_to_frames(1.0 / 3, 30) == 10
