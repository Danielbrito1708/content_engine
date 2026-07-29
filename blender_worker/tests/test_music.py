"""Tests for the music bed fade-out in scripts/edit_video.py.

Same loading trick as test_subtitles.py: the script runs inside Blender, so it is
loaded by path and only the pure helpers are exercised. The keyframing is covered
with a fake strip that records what the script asked bpy for.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "edit_video.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("edit_video", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


edit_video = _load_script()
music_fade_frames = edit_video.music_fade_frames
music_fade_start = edit_video.music_fade_start
apply_volume_fade = edit_video.apply_volume_fade


# --- music_fade_frames -------------------------------------------------------


def test_seconds_become_frames_at_the_scenes_rate():
    assert music_fade_frames({"fade_out_seconds": 1.5}, 30) == 45
    assert music_fade_frames({"fade_out_seconds": 1.5}, 60) == 90


def test_missing_block_falls_back_to_the_code_default():
    """`template.json` lives in the bucket: a template published before this
    feature has no `music` block, and the fade must still happen."""
    assert music_fade_frames(None, 30) == round(edit_video.DEFAULT_MUSIC_FADE_SECONDS * 30)
    assert music_fade_frames({}, 30) == music_fade_frames(None, 30)


def test_zero_seconds_is_how_a_template_turns_the_fade_off():
    assert music_fade_frames({"fade_out_seconds": 0}, 30) == 0


# --- music_fade_start --------------------------------------------------------


def test_fade_is_counted_back_from_the_last_frame():
    # The narration decides `last_frame`, so the same 45-frame fade lands at the
    # end of a short video and at the end of a long one.
    assert music_fade_start(900, 45, first_frame=1) == 855
    assert music_fade_start(1800, 45, first_frame=1) == 1755


def test_no_fade_when_the_template_asks_for_none():
    assert music_fade_start(900, 0, first_frame=1) is None
    assert music_fade_start(900, -30, first_frame=1) is None


def test_fade_never_starts_before_the_music_does():
    """A video shorter than the fade ramps throughout — keyframes before the
    strip's own start would leave the bed at full volume and then jump."""
    assert music_fade_start(30, 45, first_frame=1) == 1
    assert music_fade_start(60, 45, first_frame=20) == 20


def test_no_room_for_a_fade_is_not_a_fade():
    assert music_fade_start(1, 45, first_frame=1) is None
    assert music_fade_start(10, 45, first_frame=20) is None


# --- apply_volume_fade -------------------------------------------------------


class FakeStrip:
    def __init__(self):
        self.volume = 1.0
        self.keyframes = []

    def keyframe_insert(self, prop, frame=None, index=None):
        self.keyframes.append((frame, getattr(self, prop)))


def _fade(last_frame=900, fade_frames=45, first_frame=1):
    strip = FakeStrip()
    fade_start = music_fade_start(last_frame, fade_frames, first_frame)
    apply_volume_fade(
        strip,
        start_frame=first_frame,
        fade_start_frame=fade_start,
        end_frame=last_frame,
        start_volume=edit_video.DEFAULT_MUSIC_VOLUME,
    )
    return strip


def test_bed_holds_its_volume_until_the_fade_starts():
    strip = _fade()
    assert strip.keyframes[:2] == [(1, 0.2), (855, 0.2)]


def test_bed_reaches_silence_exactly_on_the_last_frame():
    strip = _fade()
    assert strip.keyframes[-1] == (900, 0.0)


def test_keyframes_are_in_chronological_order():
    """Out-of-order keyframes are what the fixed `music_fade_out` frame produced
    on a video shorter than the template it was written for."""
    frames = [frame for frame, _ in _fade(last_frame=60, fade_frames=45, first_frame=20).keyframes]
    assert frames == sorted(frames)


# --- shipped template --------------------------------------------------------


def _shipped_template():
    import json

    path = Path(__file__).resolve().parents[1] / "template.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_shipped_template_declares_no_outro():
    """The video ends with the narration — there is no closing segment, and the
    dead `outro_*` keys read as if there were one."""
    timing = _shipped_template()["timing"]
    assert "outro_start" not in timing
    assert "outro_end" not in timing


def test_shipped_template_fade_is_a_duration_not_a_frame():
    template = _shipped_template()
    assert "music_fade_out" not in template["timing"]
    frames = music_fade_frames(template.get("music"), template["frame_rate"])
    assert 0 < frames < template["frame_rate"] * 3
