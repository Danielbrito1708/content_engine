import io
import json

import pytest
from PIL import Image, ImageFont

from src.blender_worker.image.composer import (
    AssetSpec,
    Background,
    Canvas,
    CommentGuide,
    Padding,
    Position,
    Size,
    TextSpec,
    compose,
    load_guide,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def font() -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=18)


@pytest.fixture(scope="module")
def guide() -> CommentGuide:
    return CommentGuide(
        canvas=Canvas(width=400),
        background=Background(
            color=(25, 25, 25, 220),
            radius=12,
            padding=Padding(top=12, right=12, bottom=12, left=12),
        ),
        assets=[
            AssetSpec(
                id="avatar",
                minio_key="assets/avatar.png",
                size=Size(width=32, height=32),
                position=Position(x=0, y=0),
            )
        ],
        text=TextSpec(
            font_path="",
            size=18,
            color=(255, 255, 255, 255),
            offset=Position(x=40, y=0),
        ),
    )


def _make_png(width: int = 32, height: int = 32, color: tuple = (255, 0, 0, 200)) -> bytes:
    img = Image.new("RGBA", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compose_returns_valid_png(guide, font):
    result = compose(guide, "Olá", {}, font)
    assert result[:8] == PNG_MAGIC


def test_compose_canvas_width_matches_guide(guide, font):
    result = compose(guide, "Olá", {}, font)
    assert Image.open(io.BytesIO(result)).size[0] == guide.canvas.width


def test_compose_canvas_height_respects_padding(guide, font):
    result = compose(guide, "x", {}, font)
    img = Image.open(io.BytesIO(result))
    pad = guide.background.padding
    assert img.size[1] >= pad.top + pad.bottom


def test_compose_canvas_height_grows_with_long_text(guide, font):
    short = compose(guide, "Oi", {}, font)
    long = compose(
        guide,
        "Este comentário é muito longo e vai quebrar em várias linhas dentro do layout do card",
        {},
        font,
    )
    assert Image.open(io.BytesIO(long)).size[1] > Image.open(io.BytesIO(short)).size[1]


def test_compose_canvas_height_accommodates_asset(guide, font):
    result = compose(guide, "x", {"avatar": _make_png(32, 32)}, font)
    img = Image.open(io.BytesIO(result))
    pad = guide.background.padding
    assert img.size[1] >= pad.top + 32 + pad.bottom


def test_compose_missing_asset_does_not_raise(guide, font):
    result = compose(guide, "Texto sem avatar", {}, font)
    assert result[:8] == PNG_MAGIC


def test_compose_image_is_rgba(guide, font):
    result = compose(guide, "RGBA check", {}, font)
    assert Image.open(io.BytesIO(result)).mode == "RGBA"


def test_compose_tall_asset_sets_minimum_height(font):
    # canvas height is driven by spec.size, not the raw image dimensions
    tall_guide = CommentGuide(
        canvas=Canvas(width=400),
        background=Background(padding=Padding(top=12, right=12, bottom=12, left=12)),
        assets=[AssetSpec(id="banner", minio_key="", size=Size(width=32, height=120), position=Position())],
        text=TextSpec(font_path="", size=18, color=(255, 255, 255, 255), offset=Position(x=40, y=0)),
    )
    result = compose(tall_guide, "x", {"banner": _make_png(32, 120)}, font)
    img = Image.open(io.BytesIO(result))
    assert img.size[1] >= 12 + 120 + 12


def test_load_guide_from_json(tmp_path):
    data = {
        "version": "1.0",
        "canvas": {"width": 600},
        "background": {
            "color": [20, 20, 20, 200],
            "radius": 8,
            "padding": {"top": 10, "right": 10, "bottom": 10, "left": 10},
        },
        "assets": [],
        "text": {
            "font_path": "templates/fonts/inter.ttf",
            "size": 16,
            "color": [255, 255, 255, 255],
            "offset": {"x": 0, "y": 0},
        },
    }
    guide_file = tmp_path / "guide.json"
    guide_file.write_text(json.dumps(data))
    loaded = load_guide(guide_file)
    assert loaded.canvas.width == 600
    assert loaded.background.radius == 8
    assert loaded.background.padding.top == 10
    assert loaded.text.size == 16
