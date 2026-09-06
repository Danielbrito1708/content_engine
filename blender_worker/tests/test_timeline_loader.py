"""Tests for `timeline/loader.py` — the single YAML-text-to-`TimelineDoc`
entry point used by `POST /timelines/validate` (and, later, levels 2/3).

Pure — no bpy, no DB, no MinIO.
"""
from pathlib import Path

import pytest

from src.blender_worker.timeline.loader import TimelineLoadError, load_timeline

pytestmark = pytest.mark.no_db

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates_v2" / "default.yaml"


def test_valid_template_loads():
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    doc = load_timeline(text)
    assert doc.version == 2


def test_malformed_yaml_syntax_reports_yaml_location():
    with pytest.raises(TimelineLoadError) as exc_info:
        load_timeline("tracks: [{unbalanced")
    assert exc_info.value.errors[0].location == "<yaml>"


def test_non_mapping_document_reports_root_location():
    with pytest.raises(TimelineLoadError) as exc_info:
        load_timeline("- just\n- a\n- list\n")
    assert exc_info.value.errors[0].location == "<root>"


def test_missing_required_top_level_key_is_located():
    with pytest.raises(TimelineLoadError) as exc_info:
        load_timeline("""
version: 2
canvas: {width: 1080, height: 1920, fps: 30, fallback_end: 900}
inputs: {}
""")
    locations = [e.location for e in exc_info.value.errors]
    assert any("tracks" in loc for loc in locations)


def test_unknown_clip_type_is_located_precisely():
    with pytest.raises(TimelineLoadError) as exc_info:
        load_timeline("""
version: 2
canvas: {width: 1080, height: 1920, fps: 30, fallback_end: 900}
inputs: {}
tracks:
  - name: t
    channel: 1
    role: content
    clips:
      - type: nope
        start: 0s
""")
    locations = [e.location for e in exc_info.value.errors]
    assert any(loc.startswith("tracks.0.clips.0") for loc in locations)


def test_unknown_canvas_key_is_located_under_canvas():
    with pytest.raises(TimelineLoadError) as exc_info:
        load_timeline("""
version: 2
canvas: {width: 1080, height: 1920, fps: 30, fallback_end: 900, bogus: 1}
inputs: {}
tracks: []
""")
    locations = [e.location for e in exc_info.value.errors]
    assert any(loc.startswith("canvas") for loc in locations)
