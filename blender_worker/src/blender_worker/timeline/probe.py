"""Real asset durations, for the Fase 3 preview routes that use a real
`video_id` instead of caller-typed durations (`POST /timelines/validate`,
level 1, still accepts those — this module is only for levels 2/3).

No `bpy` here: `ffprobe` is a plain subprocess, called from the worker's own
async process, well before any Blender invocation happens.
"""
from __future__ import annotations

import asyncio
import subprocess


class ProbeError(RuntimeError):
    """`ffprobe` failed or produced output that isn't a duration."""


async def probe_duration_seconds(path: str, *, ffprobe_bin: str = "ffprobe") -> float:
    result = await asyncio.to_thread(
        subprocess.run,
        [
            ffprobe_bin, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ProbeError(f"ffprobe exited {result.returncode} for {path!r}: {result.stderr.strip()}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise ProbeError(f"ffprobe produced no duration for {path!r}: {result.stdout!r}") from exc


def duration_seconds_to_frames(seconds: float, frame_rate: int) -> int:
    return round(seconds * frame_rate)
