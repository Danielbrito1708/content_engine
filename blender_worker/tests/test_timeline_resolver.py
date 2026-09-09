"""Proves the timeline resolver produces the same frame numbers as the
legacy `main()` in scripts/edit_video.py, for both hook modes — the specific
promise made by Fase 1 of docs/edicao_declarativa.md.

Loads the real pure functions from edit_video.py (same importlib trick as
tests/test_intro.py — the script is not part of the installed package) and
computes one scenario twice: once through them directly, once through
`blender_worker.timeline.resolver.resolve_timeline` on
`templates_v2/default.yaml`. Where the two diverge, either the resolver has
a bug or the format cannot express what the current template does — either
way, Fase 2 (the bpy-side executor) has no business starting until this
file is green.

Pure — no bpy, no DB, no MinIO.
"""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from src.blender_worker.timeline.expr import ExprError
from src.blender_worker.timeline.resolver import TimelineResolutionError, resolve_timeline
from src.blender_worker.timeline.schema import TimelineDoc

pytestmark = pytest.mark.no_db

BLENDER_WORKER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BLENDER_WORKER_ROOT / "scripts" / "edit_video.py"
TEMPLATE_PATH = BLENDER_WORKER_ROOT / "templates_v2" / "default.yaml"
LEGACY_TEMPLATE_JSON = BLENDER_WORKER_ROOT / "template.json"


def _load_script():
    spec = importlib.util.spec_from_file_location("edit_video", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


edit_video = _load_script()


class _FakeChanStrip:
    def __init__(self, channel, frame_final_end):
        self.channel = channel
        self.frame_final_end = frame_final_end


# Scenario constants. HOOK_FRAMES is 2.6s @ 30fps — the "played" hook
# measured in blender_worker/CLAUDE.md's "Video intro" section.
FRAME_RATE = 30
HOOK_FRAMES = 78
TAIL_FRAMES = 9            # 0.3s hook-to-narration tail
INTRO_FLOOR_FRAMES = 90    # 3.0s
VOICE_FRAMES = 2000
BACKGROUND_FRAMES = 300
MUSIC_FADE_FRAMES = 45     # 1.5s


def _doc():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return TimelineDoc.model_validate(yaml.safe_load(f))


def _resolve(hook_muted):
    return resolve_timeline(
        _doc(),
        inputs={
            "background": BACKGROUND_FRAMES,
            "music": 3600,
            "voice": VOICE_FRAMES,
            "subtitles": VOICE_FRAMES,  # see resolver.py's "Known Fase 1 simplification"
            "hook": HOOK_FRAMES,
            "card": 0,  # an image has no "duration" — presence placeholder, never read
        },
        flags={"hook_muted": hook_muted},
    )


# --- equivalence against scripts/edit_video.py --------------------------


@pytest.mark.parametrize("hook_muted", [False, True])
def test_card_end_and_narration_start_match_intro_frames(hook_muted):
    resolved = _resolve(hook_muted)

    default_start = 1 + INTRO_FLOOR_FRAMES       # t["speech_start"] + 1 in the legacy template
    hook_end = 1 + HOOK_FRAMES                   # hook_strip.frame_final_end
    expected_card_end, expected_narration_start = edit_video.intro_frames(
        default_start, hook_end=hook_end, tail_frames=TAIL_FRAMES, hook_muted=hook_muted,
    )

    assert resolved.anchors["card_end"] == expected_card_end
    assert resolved.anchors["narration_start"] == expected_narration_start


@pytest.mark.parametrize("hook_muted", [False, True])
def test_timeline_end_matches_content_end_frame_plus_padding(hook_muted):
    resolved = _resolve(hook_muted)
    narration_start = resolved.anchors["narration_start"]
    card_end = resolved.anchors["card_end"]
    hook_end = 1 + HOOK_FRAMES
    voice_end = narration_start + VOICE_FRAMES

    strips = [
        _FakeChanStrip(1, 999_999),  # background bed — must be ignored
        _FakeChanStrip(2, 999_999),  # music bed — must be ignored
        _FakeChanStrip(3, voice_end),
        _FakeChanStrip(4, voice_end),  # subtitles, modelled like voice — see resolver.py
        _FakeChanStrip(5, hook_end),
        _FakeChanStrip(6, card_end),
    ]
    expected = edit_video.content_end_frame(strips, bed_channels={1, 2}, fallback=900)
    expected += edit_video.end_padding_frames({"tail_seconds": 0.5}, FRAME_RATE)

    assert resolved.timeline_end == expected


@pytest.mark.parametrize("hook_muted", [False, True])
def test_background_repeats_match(hook_muted):
    resolved = _resolve(hook_muted)
    background = next(c for c in resolved.clips if c.track == "fundo")

    expected = edit_video.background_repeats(
        clip_frames=BACKGROUND_FRAMES,
        first_start=background.frame_start,
        needed_end=resolved.timeline_end,
    )
    assert background.repeats == expected
    assert len(expected) > 0  # scenario is pointless if the background never needs to repeat


@pytest.mark.parametrize("hook_muted", [False, True])
def test_cta_starts_at_card_end_and_carries_no_frame_end(hook_muted):
    # role: bed — same reason the music/background beds never get a
    # `frame_end` either: the clip runs to timeline_end without itself
    # deciding where that is (see edit_video._apply_text_clip's docstring).
    resolved = _resolve(hook_muted)
    cta = next(c for c in resolved.clips if c.track == "cta")

    assert cta.role == "bed"
    assert cta.type == "text"
    assert cta.frame_start == resolved.anchors["card_end"]
    assert cta.frame_end is None


@pytest.mark.parametrize("hook_muted", [False, True])
def test_music_fade_start_matches(hook_muted):
    resolved = _resolve(hook_muted)
    music = next(c for c in resolved.clips if c.track == "musica")

    expected = edit_video.music_fade_start(
        last_frame=resolved.timeline_end,
        fade_frames=edit_video.music_fade_frames({"fade_out_seconds": 1.5}, FRAME_RATE),
        first_frame=music.frame_start,
    )
    assert music.fade_start == expected
    assert expected is not None  # scenario is pointless if the fade never actually starts


# --- the template file itself --------------------------------------------


def test_default_template_matches_the_shipped_template_json():
    """The proposal's worked example is more than an illustration — this
    pins its timing config to the numbers actually published in
    blender_worker/template.json. `narration.rate` is deliberately not
    compared: it is a TTS-speed setting, not part of the timeline."""
    with open(LEGACY_TEMPLATE_JSON, encoding="utf-8") as f:
        legacy = json.load(f)

    doc = _doc()
    assert doc.canvas.fps == legacy["frame_rate"]
    assert doc.canvas.fallback_end == legacy["frame_end"]
    assert doc.canvas.tail == f'{legacy["narration"]["tail_seconds"]}s'


def test_missing_required_input_fails_at_resolution_not_render():
    with pytest.raises(TimelineResolutionError, match="voice"):
        resolve_timeline(
            _doc(),
            inputs={"background": BACKGROUND_FRAMES, "music": 3600, "subtitles": VOICE_FRAMES},
            flags={"hook_muted": False},
        )


def test_optional_card_track_is_skipped_when_the_input_is_absent():
    # Unlike the hook, the card is never referenced by `after(...)` in an
    # anchor — only by `until: $card_end` on its own clip — so it can be
    # legitimately missing without the anchors failing to resolve. A
    # template resolved with no `hook` input at all is a documented gap,
    # not tested here — see resolver.py's module docstring.
    resolved = resolve_timeline(
        _doc(),
        inputs={
            "background": BACKGROUND_FRAMES, "music": 3600,
            "voice": VOICE_FRAMES, "subtitles": VOICE_FRAMES,
            "hook": HOOK_FRAMES,
        },
        flags={"hook_muted": False},
    )
    assert not any(c.track == "card" for c in resolved.clips)
    assert any(c.track == "hook" for c in resolved.clips)


# --- warnings and error attribution (Fase 3, nível 1) ---------------------


_MINIMAL_CANVAS = {"width": 1080, "height": 1920, "fps": 30, "fallback_end": 900}


def test_clip_ending_before_it_starts_is_a_warning_not_an_error():
    doc = TimelineDoc.model_validate({
        "version": 2,
        "canvas": _MINIMAL_CANVAS,
        "inputs": {},
        "tracks": [{
            "name": "broken",
            "channel": 1,
            "role": "content",
            "clips": [{"type": "image", "start": "10f", "until": "5f"}],
        }],
    })

    resolved = resolve_timeline(doc, inputs={})  # must not raise

    assert len(resolved.warnings) == 1
    assert "broken" in resolved.warnings[0]
    assert "ends before it starts" in resolved.warnings[0]


def test_anchor_error_names_the_anchor():
    doc = TimelineDoc.model_validate({
        "version": 2,
        "canvas": _MINIMAL_CANVAS,
        "inputs": {},
        "anchors": {"bad": "$typo"},
        "tracks": [],
    })

    with pytest.raises(ExprError, match=r"anchor 'bad'"):
        resolve_timeline(doc, inputs={})


def test_content_clip_error_names_the_track_and_clip_index():
    doc = TimelineDoc.model_validate({
        "version": 2,
        "canvas": _MINIMAL_CANVAS,
        "inputs": {},
        "tracks": [{
            "name": "voz",
            "channel": 3,
            "role": "content",
            "clips": [{"type": "audio", "start": "$typo"}],
        }],
    })

    with pytest.raises(ExprError, match=r"track 'voz', clip 0"):
        resolve_timeline(doc, inputs={})
