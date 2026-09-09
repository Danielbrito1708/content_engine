import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest


async def _make_template(client, name="T"):
    resp = await client.post("/templates", json={
        "name": name,
        "blend_key": f"templates/{name}.blend",
        "json_key": f"templates/{name}.json",
    })
    return resp.json()["id"]


def _mock_download(payload):
    """Patches the MinIO read behind GET /templates/{id}/config."""
    return patch(
        "src.blender_worker.api.routes.templates.download_bytes",
        new=AsyncMock(return_value=payload),
    )


async def test_create_template_returns_201(client):
    response = await client.post("/templates", json={
        "name": "Reels 30s",
        "blend_key": "templates/reels-30s.blend",
        "json_key": "templates/reels-30s.json",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Reels 30s"
    assert data["blend_key"] == "templates/reels-30s.blend"
    assert "id" in data


async def test_get_template(client):
    create = await client.post("/templates", json={
        "name": "T1",
        "blend_key": "templates/t1.blend",
        "json_key": "templates/t1.json",
    })
    tmpl_id = create.json()["id"]

    get = await client.get(f"/templates/{tmpl_id}")
    assert get.status_code == 200
    assert get.json()["id"] == tmpl_id


async def test_get_template_not_found(client):
    response = await client.get(f"/templates/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Template not found"


async def test_create_template_without_yaml_key_defaults_to_none(client):
    # yaml_key is optional at creation — a row can exist before the VSEL
    # template is published; worker.py is what refuses to render it.
    tmpl_id = await _make_template(client, "no-yaml")

    resp = await client.get(f"/templates/{tmpl_id}")
    assert resp.json()["yaml_key"] is None


async def test_create_template_accepts_a_yaml_key(client):
    response = await client.post("/templates", json={
        "name": "VSEL",
        "blend_key": "templates/vsel.blend",
        "json_key": "templates/vsel.json",
        "yaml_key": "templates/vsel.yaml",
    })
    assert response.json()["yaml_key"] == "templates/vsel.yaml"


# ── PATCH /templates/{id} ───────────────────────────────────


async def test_patch_sets_yaml_key_on_an_existing_row(client):
    tmpl_id = await _make_template(client, "retrofit")

    resp = await client.patch(f"/templates/{tmpl_id}", json={"yaml_key": "templates/retrofit.yaml"})

    assert resp.status_code == 200
    assert resp.json()["yaml_key"] == "templates/retrofit.yaml"
    assert resp.json()["id"] == tmpl_id  # same row — no new template created

    get = await client.get(f"/templates/{tmpl_id}")
    assert get.json()["yaml_key"] == "templates/retrofit.yaml"


async def test_patch_leaves_unset_fields_untouched(client):
    tmpl_id = await _make_template(client, "partial-patch")

    resp = await client.patch(f"/templates/{tmpl_id}", json={"yaml_key": "templates/x.yaml"})

    assert resp.json()["name"] == "partial-patch"
    assert resp.json()["blend_key"] == "templates/partial-patch.blend"


async def test_patch_not_found(client):
    resp = await client.patch(f"/templates/{uuid.uuid4()}", json={"yaml_key": "x.yaml"})
    assert resp.status_code == 404


# ── GET /templates/{id}/config ───────────────────────────────────


async def test_get_config_returns_parsed_json(client):
    tmpl_id = await _make_template(client, "cfg-ok")
    config = {"frame_rate": 30, "narration": {"rate": "+20%"}}

    with _mock_download(json.dumps(config).encode()):
        resp = await client.get(f"/templates/{tmpl_id}/config")

    assert resp.status_code == 200
    assert resp.json() == config


async def test_get_config_exposes_narration_rate(client):
    tmpl_id = await _make_template(client, "cfg-rate")

    with _mock_download(b'{"narration": {"rate": "-10%"}}'):
        resp = await client.get(f"/templates/{tmpl_id}/config")

    assert resp.json()["narration"]["rate"] == "-10%"


async def test_get_config_without_narration_block(client):
    """A template predating the narration block must still serve its config."""
    tmpl_id = await _make_template(client, "cfg-legacy")

    with _mock_download(b'{"frame_rate": 30, "frame_end": 900}'):
        resp = await client.get(f"/templates/{tmpl_id}/config")

    assert resp.status_code == 200
    assert "narration" not in resp.json()


async def test_get_config_downloads_the_templates_json_key(client):
    tmpl_id = await _make_template(client, "cfg-key")

    mock = AsyncMock(return_value=b"{}")
    with patch("src.blender_worker.api.routes.templates.download_bytes", new=mock):
        await client.get(f"/templates/{tmpl_id}/config")

    _, key = mock.call_args[0]
    assert key == "templates/cfg-key.json"


async def test_get_config_not_found(client):
    resp = await client.get(f"/templates/{uuid.uuid4()}/config")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Template not found"


async def test_get_config_returns_502_on_download_failure(client):
    tmpl_id = await _make_template(client, "cfg-missing")

    mock = AsyncMock(side_effect=RuntimeError("NoSuchKey"))
    with patch("src.blender_worker.api.routes.templates.download_bytes", new=mock):
        resp = await client.get(f"/templates/{tmpl_id}/config")

    assert resp.status_code == 502
    assert "Failed to fetch template config" in resp.json()["detail"]


async def test_get_config_returns_502_on_invalid_json(client):
    tmpl_id = await _make_template(client, "cfg-broken")

    with _mock_download(b"{not json"):
        resp = await client.get(f"/templates/{tmpl_id}/config")

    assert resp.status_code == 502
    assert "not valid JSON" in resp.json()["detail"]


@pytest.mark.parametrize("payload", [b"[1, 2]", b'"a string"', b"42"])
async def test_get_config_rejects_non_object_json(client, payload):
    tmpl_id = await _make_template(client, "cfg-nonobj")

    with _mock_download(payload):
        resp = await client.get(f"/templates/{tmpl_id}/config")

    assert resp.status_code == 502
    assert "must be a JSON object" in resp.json()["detail"]
