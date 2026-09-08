"""Tests for the CTA (call-to-action) overlay in scripts/edit_video.py.

Same loading trick as test_subtitles.py / test_intro.py: edit_video.py runs
inside Blender's interpreter, so it is loaded by path here and only the pure
helpers plus the bpy-side strip creation (exercised against fakes) are tested.
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
resolve_cta_style = edit_video.resolve_cta_style
wrap_cta_text = edit_video.wrap_cta_text
add_cta = edit_video.add_cta


def _exists(*present):
    known = set(present)
    return lambda path: path in known


NO_FONTS = _exists()
FUTURA_TTF = edit_video.DEFAULT_FONT_CANDIDATES[0]


# --- resolve_cta_style -------------------------------------------------------


def test_default_cta_style_is_white_with_a_black_outline():
    style = resolve_cta_style({}, exists=NO_FONTS)
    assert style["color"] == (1.0, 1.0, 1.0, 1.0)
    assert style["use_outline"] is True
    assert style["outline_color"] == (0.0, 0.0, 0.0, 1.0)
    assert style["outline_width"] > 0


def test_cta_font_size_defaults_to_a_concrete_number():
    # Unlike the subtitles, there is no pre-existing render to keep
    # pixel-identical, so the CTA gets a real default instead of "leave it
    # alone".
    assert resolve_cta_style({}, exists=NO_FONTS)["font_size"] == edit_video.DEFAULT_CTA_FONT_SIZE


def test_cta_font_size_is_configurable():
    assert resolve_cta_style({"font_size": 60}, exists=NO_FONTS)["font_size"] == 60


def test_default_cta_position_is_near_the_bottom():
    assert resolve_cta_style({}, exists=NO_FONTS)["y_position"] == edit_video.DEFAULT_CTA_Y


def test_cta_y_position_is_configurable_per_template():
    assert resolve_cta_style({"y_position": 0.2}, exists=NO_FONTS)["y_position"] == 0.2


def test_cta_y_position_is_clamped_to_the_frame():
    assert resolve_cta_style({"y_position": 1.8}, exists=NO_FONTS)["y_position"] == 1.0
    assert resolve_cta_style({"y_position": -0.4}, exists=NO_FONTS)["y_position"] == 0.0


def test_cta_color_with_wrong_channel_count_raises():
    with pytest.raises(ValueError):
        resolve_cta_style({"color": [1, 0]}, exists=NO_FONTS)


def test_cta_outline_width_is_clamped_to_blenders_range():
    assert resolve_cta_style({"outline_width": 5}, exists=NO_FONTS)["outline_width"] == 1.0
    assert resolve_cta_style({"outline_width": -1}, exists=NO_FONTS)["outline_width"] == 0.0


def test_cta_outline_can_be_disabled_from_the_template():
    assert resolve_cta_style({"use_outline": False}, exists=NO_FONTS)["use_outline"] is False


def test_cta_font_falls_back_the_same_chain_as_subtitles():
    assert resolve_cta_style({}, exists=_exists(FUTURA_TTF))["font_path"] == FUTURA_TTF
    assert resolve_cta_style({}, exists=NO_FONTS)["font_path"] is None


# --- wrap_cta_text ------------------------------------------------------------


def _measure_at(px_per_unit):
    """Measurer where width is len(text) * size * px_per_unit."""
    return lambda text, size: len(text) * size * px_per_unit


def test_short_text_fits_on_one_line():
    text = "segue o perfil"
    assert wrap_cta_text(text, 50, 10_000, _measure_at(0.5)) == text


def test_long_text_wraps_onto_multiple_lines():
    text = "me ajude a pagar a faculdade, segue o perfil"
    wrapped = wrap_cta_text(text, 50, 400, _measure_at(0.5))
    assert "\n" in wrapped
    assert " ".join(wrapped.split("\n")).replace("  ", " ") == text.replace("  ", " ")


def test_wrapped_lines_do_not_exceed_max_width():
    text = "me ajude a pagar a faculdade, segue o perfil"
    measure = _measure_at(0.5)
    wrapped = wrap_cta_text(text, 50, 400, measure)
    for line in wrapped.split("\n"):
        assert measure(line, 50) <= 400


def test_a_single_word_wider_than_max_width_is_not_split():
    measure = _measure_at(0.5)
    wrapped = wrap_cta_text("supercalifragilisticexpialidocious", 50, 10, measure)
    assert wrapped == "supercalifragilisticexpialidocious"


def test_wrap_passes_through_without_a_measurer():
    text = "me ajude a pagar a faculdade"
    assert wrap_cta_text(text, 50, 400, None) == text


def test_wrap_passes_through_without_a_font_size():
    text = "me ajude a pagar a faculdade"
    assert wrap_cta_text(text, None, 400, _measure_at(0.5)) == text


def test_wrap_passes_through_without_a_max_width():
    text = "me ajude a pagar a faculdade"
    assert wrap_cta_text(text, 50, None, _measure_at(0.5)) == text


def test_wrap_handles_empty_text():
    assert wrap_cta_text("", 50, 400, _measure_at(0.5)) == ""


# --- add_cta (bpy-side, via fakes) -------------------------------------------


class _FakeCtaStrip:
    def __init__(self, name, channel, frame_start, frame_end):
        self.name = name
        self.channel = channel
        self.frame_start = frame_start
        self.frame_end = frame_end
        self.location = [0.0, 0.0]


class _FakeSequences:
    def __init__(self):
        self.created = []

    def new_effect(self, name, type, channel, frame_start, frame_end):
        strip = _FakeCtaStrip(name, channel, frame_start, frame_end)
        strip.effect_type = type
        self.created.append(strip)
        return strip


class _FakeVSE:
    def __init__(self):
        self.sequences = _FakeSequences()


class _FakeScene:
    animation_data = None


def test_add_cta_creates_one_text_strip_on_the_given_channel():
    vse = _FakeVSE()
    strip = add_cta(
        _FakeScene(), vse, "segue o perfil", channel=7,
        frame_start=91, frame_end=900,
        style=resolve_cta_style({}, exists=NO_FONTS),
    )
    assert strip.effect_type == "TEXT"
    assert strip.channel == 7
    assert strip.frame_start == 91
    assert strip.frame_end == 900
    assert len(vse.sequences.created) == 1


def test_add_cta_spans_from_the_card_end_to_the_video_end():
    # This is the contract main() relies on: the CTA starts where the card
    # leaves and runs to the very last frame of the video.
    vse = _FakeVSE()
    strip = add_cta(
        _FakeScene(), vse, "segue o perfil", channel=7,
        frame_start=79, frame_end=723,
        style=resolve_cta_style({}, exists=NO_FONTS),
    )
    assert (strip.frame_start, strip.frame_end) == (79, 723)


def test_add_cta_centres_text_at_the_configured_y_position():
    vse = _FakeVSE()
    strip = add_cta(
        _FakeScene(), vse, "segue o perfil", channel=7,
        frame_start=1, frame_end=100,
        style=resolve_cta_style({"y_position": 0.1}, exists=NO_FONTS),
    )
    assert strip.align_x == "CENTER"
    assert strip.align_y == "CENTER"
    assert strip.location[1] == 0.1


def test_add_cta_applies_the_resolved_style():
    vse = _FakeVSE()
    strip = add_cta(
        _FakeScene(), vse, "segue o perfil", channel=7,
        frame_start=1, frame_end=100,
        style=resolve_cta_style({"font_size": 60}, exists=NO_FONTS),
    )
    assert strip.font_size == 60
    assert strip.color == (1.0, 1.0, 1.0, 1.0)
    assert strip.use_outline is True


def test_add_cta_sets_the_text_without_a_measurer():
    # No frame_width passed, no font on disk (NO_FONTS) — make_text_measurer
    # returns None, so the text goes through unwrapped rather than crashing.
    vse = _FakeVSE()
    strip = add_cta(
        _FakeScene(), vse, "me ajude a pagar a faculdade, segue o perfil",
        channel=7, frame_start=1, frame_end=100,
        style=resolve_cta_style({}, exists=NO_FONTS),
    )
    assert strip.text == "me ajude a pagar a faculdade, segue o perfil"


# --- shipped template ---------------------------------------------------------


def test_shipped_template_cta_channel_does_not_collide_with_other_channels():
    import json

    path = Path(__file__).resolve().parents[1] / "template.json"
    template = json.loads(path.read_text(encoding="utf-8"))
    channels = template.get("channels", {})
    cta_channel = channels.get("cta", edit_video.DEFAULT_CTA_CHANNEL)
    other_channels = {v for k, v in channels.items() if k != "cta"}
    assert cta_channel not in other_channels
