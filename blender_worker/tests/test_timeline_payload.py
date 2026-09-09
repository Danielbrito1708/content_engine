"""Tests for `timeline/payload.py` — the JSON shape a Fase 2 dispatcher would
actually consume, built by zipping `resolve_timeline`'s frame numbers back
with the visual/audio properties left on `ResolvedClip.clip`.

Pure — no bpy, no DB, no MinIO.
"""
from pathlib import Path

import pytest
import yaml

from src.blender_worker.timeline.expr import ExprError
from src.blender_worker.timeline.payload import build_payload
from src.blender_worker.timeline.resolver import resolve_timeline
from src.blender_worker.timeline.schema import TimelineDoc

pytestmark = pytest.mark.no_db

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates_v2" / "default.yaml"

FRAME_RATE = 30
HOOK_FRAMES = 78
VOICE_FRAMES = 2000
BACKGROUND_FRAMES = 300


def _payload(hook_muted):
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        doc = TimelineDoc.model_validate(yaml.safe_load(f))
    inputs = {
        "background": BACKGROUND_FRAMES, "music": 3600,
        "voice": VOICE_FRAMES, "subtitles": VOICE_FRAMES,
        "hook": HOOK_FRAMES, "card": 0,
    }
    flags = {"hook_muted": hook_muted}
    resolved = resolve_timeline(doc, inputs=inputs, flags=flags)
    return build_payload(doc, resolved, flags=flags)


def _clip(payload, track):
    return next(c for c in payload["clips"] if c["track"] == track)


def test_top_level_shape():
    payload = _payload(hook_muted=False)
    assert payload["frame_rate"] == FRAME_RATE
    assert payload["canvas"] == {"width": 1080, "height": 1920}
    assert payload["timeline_end"] == payload["timeline_end"]  # present, sanity


def test_background_clip_carries_its_repeats_and_min_frames():
    fundo = _clip(_payload(False), "fundo")
    assert fundo["type"] == "video"
    assert fundo["source"] == "background"
    assert fundo["frame_start"] == 1
    assert fundo["min_frames"] == 2
    assert len(fundo["repeats"]) > 0
    assert all(isinstance(r, int) for r in fundo["repeats"])


def test_music_clip_carries_its_fade():
    musica = _clip(_payload(False), "musica")
    assert musica["type"] == "audio"
    assert musica["source"] == "music"
    assert musica["volume"] == 0.2
    assert musica["fade_start"] > musica["frame_start"]
    assert musica["fade_to"] == 0.0


@pytest.mark.parametrize("hook_muted", [False, True])
def test_hook_clip_carries_the_resolved_muted_flag(hook_muted):
    hook = _clip(_payload(hook_muted), "hook")
    assert hook["type"] == "audio"
    assert hook["source"] == "hook"
    assert hook["muted"] is hook_muted


def test_voice_clip_defaults_to_unmuted():
    voz = _clip(_payload(False), "voz")
    assert voz["type"] == "audio"
    assert voz["source"] == "voice"
    assert voz["volume"] == 1.0
    assert voz["muted"] is False


def test_card_clip_carries_its_fade_out_and_position():
    card = _clip(_payload(False), "card")
    assert card["type"] == "image"
    assert card["source"] == "card"
    assert card["y_position"] == 0.5
    assert card["fade_frames"] == 4  # "4f" from the template's fade_out filter


@pytest.mark.parametrize("hook_muted", [False, True])
def test_subtitles_hide_before_matches_the_anchor_only_when_muted(hook_muted):
    payload = _payload(hook_muted)
    legendas = _clip(payload, "legendas")
    card_end = _clip(payload, "card")["frame_end"]

    assert legendas["type"] == "subtitles"
    assert legendas["source"] == "subtitles"
    if hook_muted:
        assert legendas["hide_before"] == card_end
    else:
        # Not a bare 0 like the legacy `hide_before=0 if not hook_muted`: the
        # `else: 0s` branch is still a position expression, so it gets the
        # same +1 origin shift every anchor gets — see resolver.py's
        # coordinate-system docstring. Behaviourally identical either way:
        # `drop_specs_before` keeps every spec once `frame <= 1`, since no
        # subtitle ever starts before frame 1.
        assert legendas["hide_before"] == 1


def test_subtitles_carries_timing_and_flattened_style():
    legendas = _clip(_payload(False), "legendas")

    assert legendas["max_hold_seconds"] == pytest.approx(0.4)
    assert legendas["fade_frames"] == 3
    assert legendas["rise_frames"] == 4
    assert legendas["rise_offset"] == 0.025

    style = legendas["style"]
    assert style["font_size"] == 100
    assert style["y_position"] == 0.474
    assert style["color"] == [1.0, 1.0, 1.0, 1.0]
    assert style["use_outline"] is True
    assert style["outline_color"] == [0.0, 0.0, 0.0, 1.0]
    assert style["outline_width"] == 0.24
    assert "outline" not in style  # flattened away, not left nested


def test_cta_clip_carries_text_position_and_flattened_style():
    cta = _clip(_payload(False), "cta")

    assert cta["type"] == "text"
    assert cta["role"] == "bed"
    assert "frame_end" not in cta  # bed clip — see resolver.py / apply_payload
    assert cta["text"] == "me ajude a pagar a faculdade, segue o perfil"
    assert cta["y_position"] == 0.12

    style = cta["style"]
    assert style["font_size"] == 50
    assert style["color"] == [1.0, 1.0, 1.0, 1.0]
    assert style["use_outline"] is True
    assert style["outline_color"] == [0.0, 0.0, 0.0, 1.0]
    assert style["outline_width"] == 0.24
    assert "outline" not in style


def test_bad_expression_only_reachable_from_build_payload_names_its_track():
    """`max_hold` is never resolved by `resolve_timeline` (see this module's
    docstring) — a bad expression there only surfaces here, and the error
    should still name the offending track, the same as resolver.py's own
    errors do."""
    doc = TimelineDoc.model_validate({
        "version": 2,
        "canvas": {"width": 1080, "height": 1920, "fps": 30, "fallback_end": 900},
        "inputs": {"subtitles": {"type": "srt", "required": True}},
        "tracks": [{
            "name": "legendas",
            "channel": 4,
            "role": "content",
            "clips": [{
                "type": "subtitles", "source": "$subtitles", "start": "0s",
                "max_hold": "not-a-duration",
            }],
        }],
    })
    resolved = resolve_timeline(doc, inputs={"subtitles": 100})

    with pytest.raises(ExprError, match=r"track 'legendas'"):
        build_payload(doc, resolved)
