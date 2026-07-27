import uuid

import pytest

from src.orchestrator.backgrounds import pick_background

CLIPS = [f"assets/backgrounds/bg_{i:03d}.mp4" for i in range(40)]


def test_always_returns_one_of_the_clips():
    for part in range(1, 6):
        assert pick_background(CLIPS, "run-a", part) in CLIPS


def test_is_deterministic():
    """A re-render has to reuse the same footage, or a retry silently
    produces a different video than the one already reviewed."""
    first = pick_background(CLIPS, "run-a", 2)
    assert pick_background(CLIPS, "run-a", 2) == first


def test_parts_of_the_same_run_mostly_differ():
    """Parts of one run go out back-to-back, which is where repeated footage
    would be most obvious. The seed includes the part number precisely so they
    spread out."""
    runs = [str(uuid.UUID(int=i)) for i in range(100)]
    differing = sum(
        1 for run in runs if pick_background(CLIPS, run, 1) != pick_background(CLIPS, run, 2)
    )
    assert differing >= 90


def test_spreads_across_the_library():
    chosen = {pick_background(CLIPS, str(uuid.UUID(int=i)), 1) for i in range(200)}
    assert len(chosen) > len(CLIPS) / 2


def test_single_clip_library_still_works():
    assert pick_background(["only.mp4"], "run-a", 1) == "only.mp4"


def test_empty_library_raises():
    with pytest.raises(ValueError):
        pick_background([], "run-a", 1)
