"""Tests for the time-expression mini-language (`timeline/expr.py`).

Pure — no bpy, no DB, no MinIO.
"""
import pytest

from src.blender_worker.timeline import expr

pytestmark = pytest.mark.no_db


def test_seconds_literal_converts_with_frame_rate():
    assert expr.resolve("2s", anchors={}, inputs={}, frame_rate=30) == 60


def test_frame_literal_ignores_frame_rate():
    assert expr.resolve("4f", anchors={}, inputs={}, frame_rate=30) == 4
    assert expr.resolve("4f", anchors={}, inputs={}, frame_rate=60) == 4


def test_fractional_seconds_round_to_nearest_frame():
    # 0.3s @ 30fps = 9.0 exactly; 1/30s rounds down, 2/30s rounds up.
    assert expr.resolve("0.3s", anchors={}, inputs={}, frame_rate=30) == 9
    assert expr.resolve("0.0333s", anchors={}, inputs={}, frame_rate=30) == 1
    assert expr.resolve("0.0666s", anchors={}, inputs={}, frame_rate=30) == 2


def test_anchor_reference_looks_up_relative_frame():
    assert expr.resolve("$card_end", anchors={"card_end": 91}, inputs={}, frame_rate=30) == 91


def test_unknown_anchor_reference_raises():
    with pytest.raises(expr.ExprError, match="unknown reference"):
        expr.resolve("$nope", anchors={}, inputs={}, frame_rate=30)


def test_after_returns_the_inputs_duration():
    assert expr.resolve("after($hook)", anchors={}, inputs={"hook": 78}, frame_rate=30) == 78


def test_after_on_a_missing_optional_input_raises():
    with pytest.raises(expr.ExprError, match="hook"):
        expr.resolve("after($hook)", anchors={}, inputs={"hook": None}, frame_rate=30)
    with pytest.raises(expr.ExprError, match="hook"):
        expr.resolve("after($hook)", anchors={}, inputs={}, frame_rate=30)


def test_after_only_takes_a_bare_reference():
    with pytest.raises(expr.ExprError, match="single \\$input reference"):
        expr.resolve("after(1s)", anchors={}, inputs={"hook": 1}, frame_rate=30)


def test_addition_and_subtraction():
    assert expr.resolve("after($hook) + 0.3s", anchors={}, inputs={"hook": 78}, frame_rate=30) == 87
    assert expr.resolve("10f - 4f", anchors={}, inputs={}, frame_rate=30) == 6


def test_max_and_min():
    assert expr.resolve("max(1s, 2s)", anchors={}, inputs={}, frame_rate=30) == 60
    assert expr.resolve("min(1s, 2s)", anchors={}, inputs={}, frame_rate=30) == 30
    assert expr.resolve(
        "max($intro_floor, after($hook) + 0.3s)",
        anchors={"intro_floor": 90}, inputs={"hook": 78}, frame_rate=30,
    ) == 90


def test_timeline_end_keyword_available_only_in_the_bed_pass():
    assert expr.resolve("timeline_end", anchors={}, inputs={}, frame_rate=30, timeline_end=2105) == 2105
    with pytest.raises(expr.ExprError, match="timeline_end is only available"):
        expr.resolve("timeline_end", anchors={}, inputs={}, frame_rate=30)


def test_parenthesised_expression():
    assert expr.resolve("(1s + 1s) - 0.5s", anchors={}, inputs={}, frame_rate=30) == 45


def test_unknown_function_raises():
    with pytest.raises(expr.ExprError, match="unknown function"):
        expr.resolve("nope(1s)", anchors={}, inputs={}, frame_rate=30)


def test_unreadable_text_raises():
    with pytest.raises(expr.ExprError):
        expr.resolve("1s & 2s", anchors={}, inputs={}, frame_rate=30)


def test_trailing_garbage_raises():
    with pytest.raises(expr.ExprError, match="trailing"):
        expr.resolve("1s 2s", anchors={}, inputs={}, frame_rate=30)
