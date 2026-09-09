"""Tests for the Fase 2 dispatcher (`apply_payload` and its per-type
handlers) added to `scripts/edit_video.py` — see
`docs/edicao_declarativa.md`. Same loading trick as `tests/test_intro.py`:
the script runs inside Blender, so it is loaded by path and exercised with
fakes that record what it asks bpy for.

`import_subtitles` itself is never called for real here, same as everywhere
else in this test suite (see `tests/test_subtitles.py`'s own scope) — it
reaches into `blf`, which only exists inside Blender. `_apply_subtitles_clip`
is instead tested by monkeypatching `import_subtitles` and asserting it was
called with the right arguments; `import_subtitles`'s own behaviour has its
62 tests in `test_subtitles.py`.

`apply_payload` runs in two passes — `content` clips, then a re-derived
`scene.frame_end`, then `bed` clips — because a real-Blender verification
run found the Fase 1 resolver's `timeline_end` landing 10 frames early (see
`timeline/payload.py`'s module docstring). `bed`-clip geometry
(`_apply_video_clip`'s loop, `_apply_audio_clip`'s fade) is therefore
recomputed live from real strips here too, not read off a precomputed
`repeats`/`fade_start` — these tests exercise that live recomputation, not
the (still-shipped, still advisory) payload fields.

Pure in the sense the rest of this file's siblings are: no real Blender, no
DB, no MinIO.
"""
import importlib.util
from pathlib import Path

import pytest
import yaml

from src.blender_worker.timeline.payload import build_payload
from src.blender_worker.timeline.resolver import resolve_timeline
from src.blender_worker.timeline.schema import TimelineDoc

pytestmark = pytest.mark.no_db

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "edit_video.py"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates_v2" / "default.yaml"


def _load_script():
    spec = importlib.util.spec_from_file_location("edit_video", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


edit_video = _load_script()


class FakeTransform:
    def __init__(self):
        self.offset_y = 0


class FakeStrip:
    def __init__(self, name, channel, frame_start, frame_duration=150, frame_final_duration=1):
        self.name = name
        self.channel = channel
        self.frame_start = frame_start
        self.frame_duration = frame_duration              # a plausible decodable movie clip
        self.frame_final_duration = frame_final_duration
        self.volume = 1.0
        self.mute = False
        self.blend_type = "CROSS"
        self.blend_alpha = 1.0
        self.transform = FakeTransform()
        self.keyframes = []

    @property
    def frame_final_end(self):
        return self.frame_start + self.frame_final_duration

    def keyframe_insert(self, prop, frame=None, index=None):
        self.keyframes.append((prop, frame, getattr(self, prop)))


class FakeSequences:
    def __init__(self, durations=None):
        self.created = []
        # path -> length in frames, for tests that need a strip's measured
        # length to match a specific scenario (background loop counts,
        # content_end_frame's real reach into voice/hook/card). Applied to
        # `frame_duration` for movie clips and `frame_final_duration` for
        # sound/image strips — real Blender exposes the same length under
        # different names for the two. Defaults to FakeStrip's own default
        # (150 / 1) otherwise.
        self.durations = durations or {}

    def new_movie(self, name, filepath, channel, frame_start):
        strip = FakeStrip(name, channel, frame_start, frame_duration=self.durations.get(filepath, 150))
        strip.filepath = filepath
        self.created.append(strip)
        return strip

    def new_sound(self, name, filepath, channel, frame_start):
        strip = FakeStrip(name, channel, frame_start, frame_final_duration=self.durations.get(filepath, 1))
        strip.filepath = filepath
        self.created.append(strip)
        return strip

    def new_image(self, name, filepath, channel, frame_start, fit_method=None):
        strip = FakeStrip(name, channel, frame_start)
        strip.filepath = filepath
        strip.fit_method = fit_method
        self.created.append(strip)
        return strip

    def new_effect(self, name, type, channel, frame_start, frame_end):
        strip = FakeStrip(name, channel, frame_start, frame_final_duration=frame_end - frame_start)
        strip.effect_type = type
        strip.location = [0.0, 0.0]
        self.created.append(strip)
        return strip


class FakeVSE:
    def __init__(self, durations=None):
        self.sequences = FakeSequences(durations)

    @property
    def sequences_all(self):
        return self.sequences.created


class FakeRender:
    def __init__(self):
        self.fps = None
        self.fps_base = None
        self.resolution_x = None
        self.resolution_y = None


class FakeScene:
    def __init__(self):
        self.frame_start = None
        self.frame_end = None
        self.render = FakeRender()
        self.animation_data = None


# --- _apply_video_clip ---------------------------------------------------


def test_apply_video_clip_with_loop_lays_repeats_up_to_scenes_frame_end():
    vse = FakeVSE()  # FakeStrip.frame_duration default: 150
    scene = FakeScene()
    scene.frame_end = 181
    clip = {"source": "background", "channel": 1, "frame_start": 1, "loop": True, "min_frames": 2}

    edit_video._apply_video_clip(scene, vse, clip, {"background": "/tmp/bg.mp4"}, 30)

    starts = [s.frame_start for s in vse.sequences.created]
    assert starts == [1, 151]  # 151 + 150 = 301 > 181, so only one repeat
    assert all(s.channel == 1 for s in vse.sequences.created)


def test_apply_video_clip_without_loop_lays_only_the_primary_strip():
    vse = FakeVSE()
    scene = FakeScene()
    scene.frame_end = 9999  # would need many repeats if `loop` were honoured
    clip = {"source": "background", "channel": 1, "frame_start": 1, "loop": False}

    edit_video._apply_video_clip(scene, vse, clip, {"background": "/tmp/bg.mp4"}, 30)

    assert len(vse.sequences.created) == 1


# --- _apply_audio_clip ----------------------------------------------------


def test_apply_audio_clip_sets_volume_and_mute_with_no_fade():
    vse = FakeVSE()
    clip = {"source": "hook", "channel": 5, "frame_start": 1, "volume": 1.0, "muted": True}

    edit_video._apply_audio_clip(FakeScene(), vse, clip, {"hook": "/tmp/hook.mp3"}, 30)

    strip = vse.sequences.created[0]
    assert strip.volume == 1.0
    assert strip.mute is True
    assert strip.keyframes == []


def test_apply_audio_clip_defaults_to_unmuted():
    vse = FakeVSE()
    clip = {"source": "voice", "channel": 3, "frame_start": 91, "volume": 1.0}

    edit_video._apply_audio_clip(FakeScene(), vse, clip, {"voice": "/tmp/voice.mp3"}, 30)

    assert vse.sequences.created[0].mute is False


def test_apply_audio_clip_fades_relative_to_the_real_scene_frame_end():
    vse = FakeVSE()
    scene = FakeScene()
    scene.frame_end = 2106  # already re-derived by apply_payload by the time bed clips run
    clip = {"source": "music", "channel": 2, "frame_start": 1, "volume": 0.2, "fade_duration_frames": 45}

    edit_video._apply_audio_clip(scene, vse, clip, {"music": "/tmp/music.mp3"}, 30)

    strip = vse.sequences.created[0]
    frames = [f for _, f, _ in strip.keyframes]
    volumes = [v for _, _, v in strip.keyframes]
    assert frames == [1, 2061, 2106]  # music_fade_start(2106, 45, 1) == 2061
    assert volumes == [0.2, 0.2, 0.0]


def test_apply_audio_clip_skips_a_fade_that_would_start_before_the_clip():
    # music_fade_start returns None when the fade wouldn't fit — no keyframes.
    vse = FakeVSE()
    scene = FakeScene()
    scene.frame_end = 1
    clip = {"source": "music", "channel": 2, "frame_start": 1, "volume": 0.2, "fade_duration_frames": 45}

    edit_video._apply_audio_clip(scene, vse, clip, {"music": "/tmp/music.mp3"}, 30)

    assert vse.sequences.created[0].keyframes == []


# --- _apply_image_clip -----------------------------------------------------


def test_apply_image_clip_passes_position_and_fade_through():
    vse = FakeVSE()
    scene = FakeScene()
    scene.render.resolution_y = 1920
    clip = {"source": "card", "channel": 6, "frame_start": 1, "frame_end": 91, "y_position": 0.75, "fade_frames": 4}

    edit_video._apply_image_clip(scene, vse, clip, {"card": "/tmp/card.png"}, 30)

    strip = vse.sequences.created[0]
    assert strip.transform.offset_y == 480  # card_offset_y(0.75, 1920)
    assert strip.frame_final_duration == 90


def test_apply_image_clip_falls_back_to_add_cards_own_default_fade():
    vse = FakeVSE()
    scene = FakeScene()
    scene.render.resolution_y = 1920
    # duration 6, no `fade_frames` in the payload — add_card's own default
    # (4) is capped at a third of the strip (2) either way.
    clip = {"source": "card", "channel": 6, "frame_start": 1, "frame_end": 7, "y_position": 0.5}

    edit_video._apply_image_clip(scene, vse, clip, {"card": "/tmp/card.png"}, 30)

    assert len(vse.sequences.created[0].keyframes) == 2


# --- _apply_subtitles_clip --------------------------------------------------


def test_apply_subtitles_clip_forwards_every_argument(monkeypatch):
    calls = {}

    def fake_import_subtitles(scene, vse, srt_path, channel, frame_rate, **kwargs):
        calls["positional"] = (scene, vse, srt_path, channel, frame_rate)
        calls["kwargs"] = kwargs
        return 42

    monkeypatch.setattr(edit_video, "import_subtitles", fake_import_subtitles)

    vse = FakeVSE()
    scene = FakeScene()
    scene.render.resolution_x = 1080
    clip = {
        "source": "subtitles", "channel": 4, "frame_start": 91,
        "hide_before": 91, "max_hold_seconds": 0.4, "fade_frames": 3,
        "rise_frames": 4, "rise_offset": 0.025,
        "style": {"font_size": 100, "y_position": 0.474},
    }

    edit_video._apply_subtitles_clip(scene, vse, clip, {"subtitles": "/tmp/subs.srt"}, 30)

    assert calls["positional"] == (scene, vse, "/tmp/subs.srt", 4, 30)
    kw = calls["kwargs"]
    assert kw["frame_offset"] == 91
    assert kw["fade_frames"] == 3
    assert kw["max_hold_seconds"] == 0.4
    assert kw["rise_frames"] == 4
    assert kw["rise_offset"] == 0.025
    assert kw["frame_width"] == 1080
    assert kw["hide_before"] == 91
    assert kw["style"]["font_size"] == 100


def test_apply_subtitles_clip_defaults_hide_before_to_zero(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        edit_video, "import_subtitles",
        lambda *a, **kw: calls.update(kwargs=kw),
    )
    clip = {"source": "subtitles", "channel": 4, "frame_start": 91}

    edit_video._apply_subtitles_clip(FakeScene(), FakeVSE(), clip, {"subtitles": "/tmp/s.srt"}, 30)

    assert calls["kwargs"]["hide_before"] == 0


# --- _apply_text_clip -------------------------------------------------------


def test_apply_text_clip_forwards_frame_start_and_the_scenes_frame_end(monkeypatch):
    calls = {}

    def fake_add_cta(scene, vse, text, channel, frame_start, frame_end, style, frame_width=None):
        calls.update(
            text=text, channel=channel, frame_start=frame_start,
            frame_end=frame_end, style=style, frame_width=frame_width,
        )

    monkeypatch.setattr(edit_video, "add_cta", fake_add_cta)

    scene = FakeScene()
    scene.frame_end = 2116  # already re-derived by apply_payload by the time bed clips run
    scene.render.resolution_x = 1080
    clip = {
        "type": "text", "channel": 7, "frame_start": 91,
        "text": "segue o perfil", "style": {"font_size": 50},
    }

    edit_video._apply_text_clip(scene, FakeVSE(), clip, {}, 30)

    assert calls["text"] == "segue o perfil"
    assert calls["channel"] == 7
    assert calls["frame_start"] == 91
    # Not a value read off the payload — the clip carries no frame_end at all,
    # since a `role: bed` clip's real end is decided only once frame_end is.
    assert calls["frame_end"] == 2116
    assert calls["style"]["font_size"] == 50
    assert calls["frame_width"] == 1080


# --- apply_payload (two-pass dispatch + re-derived frame_end) --------------


def _patch_handlers(monkeypatch, calls):
    for name in ("_apply_video_clip", "_apply_audio_clip", "_apply_image_clip", "_apply_subtitles_clip"):
        monkeypatch.setattr(edit_video, name, lambda *a, _n=name, **kw: calls.append(_n))


def test_apply_payload_runs_every_content_clip_before_any_bed_clip(monkeypatch):
    # Bed clips listed first in the payload on purpose — the pass order must
    # not depend on list order, only on `role`.
    calls = []
    _patch_handlers(monkeypatch, calls)

    payload = {
        "frame_rate": 30,
        "canvas": {"width": 1080, "height": 1920},
        "timeline_end": 2106,
        "tail_frames": 15,
        "clips": [
            {"type": "video", "role": "bed", "channel": 1},
            {"type": "audio", "role": "content", "channel": 3},
            {"type": "audio", "role": "bed", "channel": 2},
            {"type": "image", "role": "content", "channel": 6},
            {"type": "subtitles", "role": "content", "channel": 4},
        ],
    }
    scene = FakeScene()
    edit_video.apply_payload(scene, FakeVSE(), payload, {})

    assert scene.frame_start == 1
    assert scene.render.fps == 30
    assert scene.render.fps_base == 1.0
    assert scene.render.resolution_x == 1080
    assert scene.render.resolution_y == 1920
    assert calls == [
        "_apply_audio_clip", "_apply_image_clip", "_apply_subtitles_clip",  # content, in list order
        "_apply_video_clip", "_apply_audio_clip",                          # bed, in list order
    ]


def test_apply_payload_falls_back_to_the_advisory_timeline_end_with_no_content_strips(monkeypatch):
    # Handlers are mocked to no-ops, so no real strip ever lands on
    # vse.sequences_all — content_end_frame then has nothing to measure and
    # falls back to `timeline_end - tail_frames`, landing scene.frame_end
    # exactly back on the payload's advisory timeline_end.
    calls = []
    _patch_handlers(monkeypatch, calls)

    payload = {
        "frame_rate": 30, "canvas": {"width": 1080, "height": 1920},
        "timeline_end": 2106, "tail_frames": 15,
        "clips": [{"type": "audio", "role": "content", "channel": 3}],
    }
    scene = FakeScene()
    edit_video.apply_payload(scene, FakeVSE(), payload, {})

    assert scene.frame_end == 2106


def test_apply_payload_rejects_an_unknown_clip_type():
    payload = {
        "frame_rate": 30, "canvas": {"width": 1, "height": 1}, "timeline_end": 1, "tail_frames": 0,
        "clips": [{"type": "nope", "role": "content", "channel": 1}],
    }
    with pytest.raises(ValueError, match="unknown clip type"):
        edit_video.apply_payload(FakeScene(), FakeVSE(), payload, {})


# --- end to end: templates_v2/default.yaml -> resolver -> payload -> dispatcher


@pytest.mark.parametrize("hook_muted", [False, True])
def test_the_default_template_resolves_and_dispatches_without_a_real_blender(monkeypatch, hook_muted):
    """Closes the Fase 1 -> Fase 2 loop: the actual proposal example, run
    through the actual resolver and payload builder, produces bpy calls that
    line up with what `main()` creates today for the same scenario — same
    channels, same frame numbers, same subtitle-hiding rule, same fade.

    `import_subtitles` is still swapped for a spy — see this file's module
    docstring for why (`blf` is Blender-only). Because of that, this fake
    world's `content_end_frame` never sees real subtitle strips, only voice/
    hook/card — which happens to still land on the same maximum the pure
    resolver assumed for this scenario (voice is the longest either way), so
    `scene.frame_end == resolved.timeline_end` here. A *real* Blender run
    (recorded in `docs/edicao_declarativa.md`) is what actually exercises
    the gap between the two — see `timeline/payload.py`'s module docstring.
    """
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        doc = TimelineDoc.model_validate(yaml.safe_load(f))

    inputs = {"background": 300, "music": 3600, "voice": 2000, "subtitles": 2000, "hook": 78, "card": 0}
    flags = {"hook_muted": hook_muted}
    resolved = resolve_timeline(doc, inputs=inputs, flags=flags)
    payload = build_payload(doc, resolved, flags=flags)

    subtitle_calls = []
    monkeypatch.setattr(
        edit_video, "import_subtitles",
        lambda *a, **kw: subtitle_calls.append((a, kw)) or 0,
    )
    # Same reason import_subtitles is spied instead of run for real: add_cta
    # reaches into blf/bpy.data.fonts, which only exist inside Blender.
    cta_calls = []
    monkeypatch.setattr(
        edit_video, "add_cta",
        lambda *a, **kw: cta_calls.append((a, kw)),
    )

    # Matches `inputs` above — content_end_frame reads voice/hook's real
    # (faked) length exactly like it would a real strip's; card's own length
    # comes from add_card itself (frame_end - frame_start), no faking needed.
    vse = FakeVSE(durations={"/tmp/bg.mp4": 300, "/tmp/voice.mp3": 2000, "/tmp/hook.mp3": 78})
    scene = FakeScene()
    assets = {
        "background": "/tmp/bg.mp4", "music": "/tmp/music.mp3", "voice": "/tmp/voice.mp3",
        "subtitles": "/tmp/subs.srt", "hook": "/tmp/hook.mp3", "card": "/tmp/card.png",
    }
    edit_video.apply_payload(scene, vse, payload, assets)

    assert scene.frame_end == resolved.timeline_end
    by_channel = {}
    for s in vse.sequences.created:
        by_channel.setdefault(s.channel, []).append(s)

    # fundo (ch1): the primary clip plus every repeat needed to reach the
    # (re-derived, here identical) scene.frame_end — recomputed live by
    # extend_background, not read off payload["repeats"].
    from src.blender_worker.timeline.resolver import _background_repeats  # relative-space twin, for the expected count
    fundo_payload = next(c for c in payload["clips"] if c["track"] == "fundo")
    expected_repeats = _background_repeats(300, fundo_payload["frame_start"] - 1, scene.frame_end - 1)
    assert len(by_channel[1]) == 1 + len(expected_repeats)

    # musica (ch2): starts at 0.2, faded to 0 by the scene's own frame_end.
    music = by_channel[2][0]
    assert (music.keyframes[0][1], music.keyframes[0][2]) == (music.frame_start, 0.2)
    assert (music.keyframes[-1][1], music.keyframes[-1][2]) == (scene.frame_end, 0.0)

    # hook (ch5): muted exactly when the flag says so.
    assert by_channel[5][0].mute is hook_muted
    # card (ch6): duration matches the card_end anchor.
    card = by_channel[6][0]
    assert card.frame_start == resolved.anchors["card_end"] - card.frame_final_duration
    # voz (ch3): starts at narration_start, never muted.
    assert by_channel[3][0].frame_start == resolved.anchors["narration_start"]
    assert by_channel[3][0].mute is False
    # legendas: one import_subtitles call, hiding before the card exactly
    # when the hook is muted.
    assert len(subtitle_calls) == 1
    _, kwargs = subtitle_calls[0]
    assert kwargs["frame_offset"] == resolved.anchors["narration_start"]
    expected_hide_before = resolved.anchors["card_end"] if hook_muted else 1
    assert kwargs["hide_before"] == expected_hide_before
    # cta (ch7): starts where the card leaves, runs to the real frame_end —
    # never in `by_channel` since add_cta itself is spied here, not run.
    assert len(cta_calls) == 1
    cta_args, _ = cta_calls[0]
    _, _, cta_text, cta_channel, cta_start, cta_end, _cta_style = cta_args
    assert cta_text == "me ajude a pagar a faculdade, segue o perfil"
    assert cta_channel == 7
    assert cta_start == resolved.anchors["card_end"]
    assert cta_end == scene.frame_end


def test_apply_payload_extends_frame_end_when_the_real_subtitles_outlast_the_voice(monkeypatch):
    """Regression test for the exact gap a real-Blender verification run
    found: `templates_v2/default.yaml` models a `subtitles` clip's duration
    as the voice's own (`duration: source` off the `voice` input — see
    resolver.py's "Known Fase 1 simplification"), but a real SRT's last word
    can legitimately hold past where the voice audio itself ends. There, the
    true content end was 10 frames later than the Fase 1 resolver's
    `timeline_end` — every other strip matched exactly. This test fakes that
    exact shape (a subtitle strip ending 10 frames after voice) without
    needing Blender, and asserts `scene.frame_end` follows it.
    """
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        doc = TimelineDoc.model_validate(yaml.safe_load(f))

    inputs = {"background": 300, "music": 3600, "voice": 2000, "subtitles": 2000, "hook": 78, "card": 0}
    resolved = resolve_timeline(doc, inputs=inputs, flags={"hook_muted": False})
    payload = build_payload(doc, resolved, flags={"hook_muted": False})

    overshoot = 10

    def fake_import_subtitles(scene, vse, srt_path, channel, frame_rate, frame_offset, **kwargs):
        # Stands in for what a real SRT's held last word would create: one
        # text strip reaching past the voice clip's own end.
        voice_end = next(c["frame_end"] for c in payload["clips"] if c["track"] == "voz")
        strip = FakeStrip("sub_0000", channel, frame_offset, frame_final_duration=(voice_end + overshoot) - frame_offset)
        vse.sequences.created.append(strip)
        return 1

    monkeypatch.setattr(edit_video, "import_subtitles", fake_import_subtitles)
    monkeypatch.setattr(edit_video, "add_cta", lambda *a, **kw: None)

    vse = FakeVSE(durations={"/tmp/bg.mp4": 300, "/tmp/voice.mp3": 2000, "/tmp/hook.mp3": 78})
    scene = FakeScene()
    assets = {
        "background": "/tmp/bg.mp4", "music": "/tmp/music.mp3", "voice": "/tmp/voice.mp3",
        "subtitles": "/tmp/subs.srt", "hook": "/tmp/hook.mp3", "card": "/tmp/card.png",
    }
    edit_video.apply_payload(scene, vse, payload, assets)

    assert scene.frame_end == resolved.timeline_end + overshoot
