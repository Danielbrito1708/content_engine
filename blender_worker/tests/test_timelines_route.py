"""Tests for `POST /timelines/validate` and `GET /timelines/schema` — Fase 3,
nível 1 of docs/edicao_declarativa.md. No DB, no MinIO, no Blender: these
routes are pure resolution over a caller-supplied YAML string.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates_v2" / "default.yaml"
TEMPLATE_TEXT = TEMPLATE_PATH.read_text(encoding="utf-8")

VALID_INPUTS = {
    "background": 10.0,
    "music": 120.0,
    "voice": 200 / 3,
    "subtitles": 200 / 3,
    "hook": 2.6,
    "card": 0.0,
}


async def test_valid_template_and_inputs_resolve_successfully(client):
    resp = await client.post(
        "/timelines/validate",
        json={"template": TEMPLATE_TEXT, "inputs": VALID_INPUTS},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["clips"]
    assert body["warnings"] == []
    assert body["errors"] == []
    assert body["timeline_end"] is not None


async def test_missing_required_input_reports_error_not_transport_failure(client):
    inputs = {k: v for k, v in VALID_INPUTS.items() if k != "voice"}
    resp = await client.post(
        "/timelines/validate",
        json={"template": TEMPLATE_TEXT, "inputs": inputs},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["errors"]
    assert any("voice" in e["message"] for e in body["errors"])


async def test_typo_reference_in_anchor_is_reported(client):
    bad_template = """
version: 2
canvas: {width: 1080, height: 1920, fps: 30, fallback_end: 900}
inputs: {}
anchors:
  bad: $typo
tracks: []
"""
    resp = await client.post("/timelines/validate", json={"template": bad_template})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["errors"]


async def test_clip_ending_before_it_starts_is_a_warning_not_an_error(client):
    template = """
version: 2
canvas: {width: 1080, height: 1920, fps: 30, fallback_end: 900}
inputs: {}
tracks:
  - name: broken
    channel: 1
    role: content
    clips:
      - {type: image, start: 10f, until: 5f}
"""
    resp = await client.post("/timelines/validate", json={"template": template})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["errors"] == []
    assert body["warnings"]


async def test_malformed_yaml_reports_yaml_location(client):
    resp = await client.post("/timelines/validate", json={"template": "tracks: [{"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["errors"][0]["location"] == "<yaml>"


async def test_missing_template_field_is_a_request_validation_error(client):
    resp = await client.post("/timelines/validate", json={"inputs": {}})
    assert resp.status_code == 422


async def test_schema_endpoint_returns_the_pydantic_json_schema(client):
    resp = await client.get("/timelines/schema")
    assert resp.status_code == 200
    body = resp.json()
    assert {"$defs", "properties", "required", "title", "type"} <= body.keys()
    assert "tracks" in body["required"]
