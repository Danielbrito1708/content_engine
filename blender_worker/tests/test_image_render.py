import io

import pytest
from PIL import Image, ImageFont


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_font(monkeypatch):
    font = ImageFont.load_default(size=18)
    monkeypatch.setattr("src.blender_worker.api.routes.images.load_font", lambda *_: font)


@pytest.fixture
def mock_s3(monkeypatch):
    img = Image.new("RGBA", (48, 48), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    class _Body:
        def read(self):
            return png_bytes

    class _FakeS3:
        def get_object(self, **_):
            return {"Body": _Body()}

        def put_object(self, **_):
            pass

    monkeypatch.setattr(
        "src.blender_worker.api.routes.images.get_s3_client", lambda: _FakeS3()
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_render_unknown_template_returns_404(client):
    response = await client.post("/images/render", json={
        "template": "does_not_exist",
        "text": "Oi",
    })
    assert response.status_code == 404
    assert "does_not_exist" in response.json()["detail"]


async def test_render_returns_201_with_output_key(client, mock_font, mock_s3):
    response = await client.post("/images/render", json={
        "template": "comment_default",
        "text": "Este é um comentário de teste",
    })
    assert response.status_code == 201
    data = response.json()
    assert "output_key" in data
    assert data["output_key"].endswith(".png")


async def test_render_output_key_is_unique_per_request(client, mock_font, mock_s3):
    r1 = await client.post("/images/render", json={"template": "comment_default", "text": "a"})
    r2 = await client.post("/images/render", json={"template": "comment_default", "text": "b"})
    assert r1.json()["output_key"] != r2.json()["output_key"]


async def test_render_respects_custom_output_key(client, mock_font, mock_s3):
    response = await client.post("/images/render", json={
        "template": "comment_default",
        "text": "Teste",
        "output_key": "custom/resultado.png",
    })
    assert response.status_code == 201
    assert response.json()["output_key"] == "custom/resultado.png"


async def test_render_missing_request_fields_returns_422(client):
    response = await client.post("/images/render", json={})
    assert response.status_code == 422
