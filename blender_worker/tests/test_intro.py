"""Tests for the intro (comment card + hook narration) in scripts/edit_video.py.

Same loading trick as test_subtitles.py: the script runs inside Blender, so it
is loaded by path and only the pure helpers are exercised. The strip creation is
covered with fakes that record what the script asked bpy for.
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
intro_frames = edit_video.intro_frames
drop_specs_before = edit_video.drop_specs_before
card_offset_y = edit_video.card_offset_y


# --- intro_frames ------------------------------------------------------------


def test_no_hook_keeps_the_templates_speech_start():
    assert intro_frames(91, hook_end=None, tail_frames=9) == (91, 91)


def test_hook_end_of_zero_is_treated_as_no_hook():
    assert intro_frames(91, hook_end=0, tail_frames=9) == (91, 91)


def test_narration_waits_for_a_hook_longer_than_the_intro():
    # Hook ends at 150, plus a 9-frame beat: the narration cannot start at 91.
    assert intro_frames(91, hook_end=150, tail_frames=9) == (159, 159)


def test_short_hook_does_not_shorten_the_template_intro():
    # The template's own intro is the floor — a 1s hook keeps the 3s opening.
    assert intro_frames(91, hook_end=30, tail_frames=9) == (91, 91)


def test_tail_is_optional():
    assert intro_frames(91, hook_end=150) == (150, 150)


def test_tail_pushes_the_narration_past_the_last_hook_frame():
    # Without the tail the first narrated word would land on the frame the hook
    # ends, which reads as one run-on sentence.
    _, narration_start = intro_frames(1, hook_end=200, tail_frames=9)
    assert narration_start - 200 == 9


# --- intro_frames, hook muted ------------------------------------------------


def test_muted_hook_lets_the_narration_start_with_the_video():
    # The narration itself says the phrase, so there is nothing to wait for.
    _, narration_start = intro_frames(91, hook_end=79, tail_frames=9, hook_muted=True)
    assert narration_start == 1


def test_muted_hook_holds_the_card_for_the_length_of_the_phrase():
    card_end, _ = intro_frames(91, hook_end=79, tail_frames=9, hook_muted=True)
    assert card_end == 79


def test_muted_hook_is_not_charged_the_tail():
    """O tail separa dois áudios; mudo há um só, e cobrá-lo esconde a legenda da
    primeira palavra da história — medido: gancho falado acaba em 2,560s e a
    palavra seguinte começa em 2,759s, dentro de um tail de 0,3s."""
    no_tail, _ = intro_frames(91, hook_end=79, tail_frames=0, hook_muted=True)
    with_tail, _ = intro_frames(91, hook_end=79, tail_frames=30, hook_muted=True)
    assert no_tail == with_tail == 79


def test_muted_hook_has_no_template_floor():
    # Holding a short card to the template's 3s would cover the story's second
    # sentence, which is already being narrated underneath it.
    card_end, _ = intro_frames(91, hook_end=30, tail_frames=9, hook_muted=True)
    assert card_end == 30


def test_muted_hook_respects_a_custom_intro_start():
    _, narration_start = intro_frames(91, hook_end=79, hook_muted=True, intro_start=4)
    assert narration_start == 4


def test_muting_without_a_hook_changes_nothing():
    assert intro_frames(91, hook_end=None, hook_muted=True) == (91, 91)


# --- drop_specs_before -------------------------------------------------------


def _spec(start):
    return {"start": start, "end": start + 5, "text": "x"}


def test_specs_before_the_frame_are_dropped():
    specs = [_spec(1), _spec(40), _spec(88), _spec(120)]
    assert [s["start"] for s in drop_specs_before(specs, 88)] == [88, 120]


def test_zero_keeps_every_spec():
    specs = [_spec(1), _spec(40)]
    assert drop_specs_before(specs, 0) == specs


def test_surviving_specs_keep_their_own_timing():
    # The words that stay are not shifted — the audio did not move.
    specs = [_spec(1), _spec(100)]
    assert drop_specs_before(specs, 50) == [_spec(100)]


# --- card_offset_y -----------------------------------------------------------


def test_centred_card_has_no_offset():
    assert card_offset_y(0.5, 1920) == 0


def test_higher_position_offsets_upwards():
    # 0.75 is a quarter of the frame above centre; Blender's +y is up.
    assert card_offset_y(0.75, 1920) == 480


def test_lower_position_offsets_downwards():
    assert card_offset_y(0.25, 1920) == -480


def test_position_is_clamped_to_the_frame():
    # Off-frame values render as a card that is silently missing, so they are
    # pulled back to the edge instead.
    assert card_offset_y(5.0, 1920) == 960
    assert card_offset_y(-5.0, 1920) == -960


def test_offset_is_a_whole_pixel():
    assert isinstance(card_offset_y(0.474, 1920), int)


# --- add_card ----------------------------------------------------------------


class FakeTransform:
    def __init__(self):
        self.offset_y = 0


class FakeStrip:
    def __init__(self, name, channel, frame_start):
        self.name = name
        self.channel = channel
        self.frame_start = frame_start
        self.frame_final_duration = 1
        self.blend_type = "CROSS"
        self.blend_alpha = 1.0
        self.transform = FakeTransform()
        self.keyframes = []

    def keyframe_insert(self, prop, frame=None, index=None):
        self.keyframes.append((prop, frame, getattr(self, prop)))


class FakeSequences:
    def __init__(self):
        self.created = []

    def new_image(self, name, filepath, channel, frame_start, fit_method=None):
        strip = FakeStrip(name, channel, frame_start)
        strip.filepath = filepath
        strip.fit_method = fit_method
        self.created.append(strip)
        return strip


class FakeVSE:
    def __init__(self):
        self.sequences = FakeSequences()


class FakeScene:
    animation_data = None


def _add_card(**kwargs):
    vse = FakeVSE()
    scene = FakeScene()
    defaults = dict(
        path="/tmp/card.png",
        channel=6,
        frame_start=1,
        frame_end=91,
        config={},
        frame_height=1920,
    )
    defaults.update(kwargs)
    strip = edit_video.add_card(
        scene,
        vse,
        defaults["path"],
        defaults["channel"],
        defaults["frame_start"],
        defaults["frame_end"],
        defaults["config"],
        defaults["frame_height"],
    )
    return strip


def test_card_covers_the_whole_intro():
    strip = _add_card(frame_start=1, frame_end=91)
    assert strip.frame_start == 1
    assert strip.frame_final_duration == 90


def test_card_is_composited_with_alpha():
    # Without ALPHA_OVER the transparent frame around the card renders black.
    assert _add_card().blend_type == "ALPHA_OVER"


def test_card_keeps_its_original_pixel_size():
    assert _add_card().fit_method == "ORIGINAL"


def test_card_is_positioned_from_the_guide():
    strip = _add_card(config={"y_position": 0.75})
    assert strip.transform.offset_y == 480


def test_card_fades_out_only():
    strip = _add_card(frame_start=1, frame_end=91, config={"fade_frames": 4})
    frames = [(f, alpha) for _, f, alpha in strip.keyframes]
    assert (87, 1.0) in frames
    assert (91, 0.0) in frames
    # No ramp at the head: the card is already there on the first frame.
    assert [alpha for f, alpha in frames if f <= 87] == [1.0]


def test_card_is_opaque_from_its_first_frame():
    # Two videos opening on the same fade read as the same opening; the card
    # landing already up is what breaks that signature.
    strip = _add_card(frame_start=1, frame_end=91, config={"fade_frames": 4})
    assert (1, 0.0) not in [(f, alpha) for _, f, alpha in strip.keyframes]


def test_fade_is_capped_at_a_third_of_the_strip():
    # A fade longer than the strip would leave the card half-transparent for its
    # whole life instead of ever reaching full opacity.
    strip = _add_card(frame_start=1, frame_end=7, config={"fade_frames": 30})
    fade_starts = [f for _, f, alpha in strip.keyframes if alpha == 1.0]
    assert min(fade_starts) == 5


def test_fade_can_be_switched_off():
    strip = _add_card(config={"fade_frames": 0})
    assert strip.keyframes == []
    assert strip.blend_alpha == 1.0


def test_card_without_frame_height_is_left_centred():
    strip = _add_card(frame_height=None, config={"y_position": 0.75})
    assert strip.transform.offset_y == 0
