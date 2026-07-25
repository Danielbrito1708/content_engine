"""Tests for the subtitle timeline logic in scripts/edit_video.py.

edit_video.py runs inside Blender's interpreter, so it is loaded by path here.
Only the pure helpers are exercised — bpy is imported inside main(), never at
module level, so importing the script outside Blender is safe.
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
build_subtitle_timeline = edit_video.build_subtitle_timeline
parse_srt = edit_video.parse_srt
ts_to_frame = edit_video.ts_to_frame


def _entry(start, end, text):
    """Build an SRT entry tuple from seconds."""
    def fmt(t):
        return f"00:00:{int(t):02d},{int(round((t % 1) * 1000)):03d}"
    return (fmt(start), fmt(end), text)


# --- ts_to_frame ---------------------------------------------------------


def test_ts_to_frame_rounds_to_nearest():
    assert ts_to_frame("00:00:01,000", 30) == 30
    assert ts_to_frame("00:00:00,340", 30) == 10  # 10.2 → 10
    assert ts_to_frame("00:00:00,360", 30) == 11  # 10.8 → 11 (int() gave 10)


def test_ts_to_frame_handles_hours_and_minutes():
    assert ts_to_frame("01:02:03,500", 30) == (3600 + 120 + 3) * 30 + 15


# --- parse_srt -----------------------------------------------------------


def test_parse_srt_reads_word_level_entries(tmp_path):
    srt = tmp_path / "words.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:00,320\nBem-vindo\n\n"
        "2\n00:00:00,320 --> 00:00:00,540\nao\n",
        encoding="utf-8",
    )
    entries = parse_srt(str(srt))
    assert len(entries) == 2
    assert entries[0][2] == "Bem-vindo"
    assert entries[1] == ("00:00:00,320", "00:00:00,540", "ao")


# --- build_subtitle_timeline --------------------------------------------


def test_one_strip_per_word():
    entries = [_entry(0, 0.3, "uma"), _entry(0.3, 0.6, "palavra"), _entry(0.6, 1.0, "só")]
    specs = build_subtitle_timeline(entries, 30)
    assert [s["text"] for s in specs] == ["uma", "palavra", "só"]


def test_word_holds_until_next_word_starts():
    # Gap of 200ms between the two words: the first should stretch to cover it.
    entries = [_entry(0, 0.3, "uma"), _entry(0.5, 0.9, "palavra")]
    specs = build_subtitle_timeline(entries, 30, max_hold_seconds=1.0)
    assert specs[0]["end"] == specs[1]["start"]


def test_hold_is_capped_by_max_hold_seconds():
    # 2s pause — the word must not linger through it.
    entries = [_entry(0, 0.3, "fim"), _entry(2.3, 2.7, "recomeço")]
    specs = build_subtitle_timeline(entries, 30, max_hold_seconds=0.4)
    assert specs[0]["end"] == ts_to_frame("00:00:00,300", 30) + 12
    assert specs[0]["end"] < specs[1]["start"]


def test_strips_never_overlap():
    entries = [_entry(0, 0.5, "a"), _entry(0.3, 0.8, "b"), _entry(0.7, 1.2, "c")]
    specs = build_subtitle_timeline(entries, 30, max_hold_seconds=0.4)
    for current, following in zip(specs, specs[1:]):
        assert current["end"] <= following["start"]


def test_every_strip_has_positive_duration():
    # Words shorter than a single frame at 30fps.
    entries = [_entry(0, 0.01, "a"), _entry(0.05, 0.06, "b"), _entry(0.1, 0.5, "c")]
    specs = build_subtitle_timeline(entries, 30, max_hold_seconds=0)
    assert specs
    for spec in specs:
        assert spec["end"] > spec["start"]


def test_words_landing_on_the_same_frame_are_merged_not_dropped():
    entries = [_entry(0.0, 0.01, "e"), _entry(0.01, 0.02, "aí"), _entry(0.5, 0.9, "gente")]
    specs = build_subtitle_timeline(entries, 30)
    assert len(specs) == 2
    assert specs[0]["text"] == "e aí"


def test_frame_offset_shifts_every_strip():
    entries = [_entry(0, 0.3, "uma"), _entry(0.3, 0.6, "palavra")]
    base = build_subtitle_timeline(entries, 30)
    shifted = build_subtitle_timeline(entries, 30, frame_offset=91)
    for b, s in zip(base, shifted):
        assert s["start"] == b["start"] + 91
        assert s["end"] == b["end"] + 91


def test_no_fade_between_adjacent_words():
    entries = [_entry(0, 0.5, "uma"), _entry(0.5, 1.0, "palavra"), _entry(1.0, 1.5, "só")]
    specs = build_subtitle_timeline(entries, 30)
    assert specs[0]["fade_out"] == 0
    assert specs[1]["fade_in"] == 0
    assert specs[1]["fade_out"] == 0
    assert specs[2]["fade_in"] == 0


def test_fades_only_at_the_edges_of_a_gap():
    entries = [_entry(0, 0.5, "antes"), _entry(3.0, 3.5, "depois")]
    specs = build_subtitle_timeline(entries, 30, max_hold_seconds=0.4)
    assert specs[0]["fade_in"] > 0   # first strip overall
    assert specs[0]["fade_out"] > 0  # gap follows
    assert specs[1]["fade_in"] > 0   # gap precedes
    assert specs[1]["fade_out"] > 0  # last strip overall


def test_fade_never_exceeds_a_third_of_the_strip():
    # 4-frame word followed by a gap: fade of 3 would invert the keyframes.
    entries = [_entry(0, 0.13, "curta"), _entry(3.0, 3.5, "longa")]
    specs = build_subtitle_timeline(entries, 30, fade_frames=3, max_hold_seconds=0)
    duration = specs[0]["end"] - specs[0]["start"]
    assert specs[0]["fade_in"] <= duration // 3
    assert specs[0]["start"] + specs[0]["fade_in"] <= specs[0]["end"] - specs[0]["fade_out"]


def test_fade_frames_zero_disables_fades():
    entries = [_entry(0, 0.5, "antes"), _entry(3.0, 3.5, "depois")]
    specs = build_subtitle_timeline(entries, 30, fade_frames=0)
    assert all(s["fade_in"] == 0 and s["fade_out"] == 0 for s in specs)


# --- rise (entrance animation) -------------------------------------------


def test_every_word_rises_including_adjacent_ones():
    # Unlike fades, the rise applies to every word — that's the per-word pop.
    entries = [_entry(0, 0.5, "uma"), _entry(0.5, 1.0, "palavra"), _entry(1.0, 1.5, "só")]
    specs = build_subtitle_timeline(entries, 30, rise_frames=4)
    assert all(s["rise"] == 4 for s in specs)


def test_rise_leaves_a_frame_at_rest():
    # A 4-frame word must reach its resting position before the strip ends.
    entries = [_entry(0, 0.13, "curta"), _entry(3.0, 3.5, "longa")]
    specs = build_subtitle_timeline(entries, 30, rise_frames=4, max_hold_seconds=0)
    duration = specs[0]["end"] - specs[0]["start"]
    assert specs[0]["rise"] <= duration - 1
    assert specs[0]["start"] + specs[0]["rise"] < specs[0]["end"]


def test_rise_never_exceeds_strip_duration():
    entries = [_entry(0, 0.01, "a"), _entry(0.05, 0.06, "b"), _entry(0.1, 0.5, "c")]
    specs = build_subtitle_timeline(entries, 30, rise_frames=4, max_hold_seconds=0)
    for spec in specs:
        assert spec["rise"] <= max(0, spec["end"] - spec["start"] - 1)


def test_rise_frames_zero_disables_the_animation():
    entries = [_entry(0, 0.5, "uma"), _entry(0.5, 1.0, "palavra")]
    specs = build_subtitle_timeline(entries, 30, rise_frames=0)
    assert all(s["rise"] == 0 for s in specs)


def test_rise_is_independent_of_fade():
    # fades off, rise still on — the two animations are orthogonal
    entries = [_entry(0, 0.5, "uma"), _entry(3.0, 3.5, "outra")]
    specs = build_subtitle_timeline(entries, 30, fade_frames=0, rise_frames=4)
    assert all(s["fade_in"] == 0 and s["fade_out"] == 0 for s in specs)
    assert all(s["rise"] > 0 for s in specs)


def test_empty_srt_produces_no_strips():
    assert build_subtitle_timeline([], 30) == []


def test_blank_entries_are_skipped():
    entries = [_entry(0, 0.3, ""), _entry(0.5, 0.9, "palavra")]
    specs = build_subtitle_timeline(entries, 30)
    assert len(specs) == 1
    assert specs[0]["text"] == "palavra"
