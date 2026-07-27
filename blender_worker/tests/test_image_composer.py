import io
import json

import pytest
from PIL import Image, ImageFont

from src.blender_worker.image.composer import (
    BLUR_EXTENT,
    AssetSpec,
    Background,
    Canvas,
    CommentGuide,
    Padding,
    Position,
    Shadow,
    Size,
    TextSpec,
    compose,
    load_guide,
    shadow_margins,
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


# ── Shadow ────────────────────────────────────────────────────────────────────

CARD_W = 400
RADIUS = 12


def _shadow_guide(shadow: Shadow | None = None) -> CommentGuide:
    return CommentGuide(
        canvas=Canvas(width=CARD_W),
        background=Background(
            color=(25, 25, 25, 220),
            radius=RADIUS,
            padding=Padding(top=12, right=12, bottom=12, left=12),
            shadow=shadow or Shadow(),
        ),
        assets=[],
        text=TextSpec(font_path="", size=18, color=(255, 255, 255, 255), offset=Position()),
    )


def _alpha(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGBA").getchannel("A")


def test_shadow_margins_are_zero_when_disabled():
    assert shadow_margins(Shadow(enabled=False, blur=20, offset=Position(x=5, y=5))) == (0, 0, 0, 0)


def test_shadow_margins_follow_offset_direction():
    # offset pushes the shadow down-right, so it needs more room on the right and bottom
    left, top, right, bottom = shadow_margins(
        Shadow(enabled=True, blur=10, spread=0, offset=Position(x=4, y=6))
    )
    reach = 10 * BLUR_EXTENT
    assert (left, top, right, bottom) == (reach - 4, reach - 6, reach + 4, reach + 6)


def test_shadow_margins_never_negative():
    # an offset larger than the blur reach would otherwise produce a negative margin
    assert shadow_margins(Shadow(enabled=True, blur=1, offset=Position(x=50, y=50)))[:2] == (0, 0)


def test_disabled_shadow_leaves_geometry_unchanged(font):
    """The pre-shadow behaviour is the default: canvas is exactly the card."""
    result = compose(_shadow_guide(), "Sem sombra", {}, font)
    assert Image.open(io.BytesIO(result)).size[0] == CARD_W


def test_shadow_grows_canvas_by_exactly_the_margins(font):
    shadow = Shadow(enabled=True, blur=8, offset=Position(x=0, y=5))
    plain = Image.open(io.BytesIO(compose(_shadow_guide(), "Card", {}, font))).size
    shaded = Image.open(io.BytesIO(compose(_shadow_guide(shadow), "Card", {}, font))).size

    left, top, right, bottom = shadow_margins(shadow)
    assert shaded == (plain[0] + left + right, plain[1] + top + bottom)


def test_shadow_does_not_clip_at_canvas_edge(font):
    """The margin must cover the blur's full reach, or the falloff ends in a hard line."""
    png = compose(_shadow_guide(Shadow(enabled=True, blur=10, offset=Position(x=0, y=6))), "Card", {}, font)
    alpha = _alpha(png)
    w, h = alpha.size

    border = (
        [alpha.getpixel((x, 0)) for x in range(w)]
        + [alpha.getpixel((x, h - 1)) for x in range(w)]
        + [alpha.getpixel((0, y)) for y in range(h)]
        + [alpha.getpixel((w - 1, y)) for y in range(h)]
    )
    assert max(border) == 0


def test_shadow_is_visible_below_the_card(font):
    shadow = Shadow(enabled=True, blur=10, offset=Position(x=0, y=8))
    png = compose(_shadow_guide(shadow), "Card", {}, font)
    alpha = _alpha(png)
    w, h = alpha.size
    _, _, _, margin_b = shadow_margins(shadow)

    # a few px under the card's bottom edge, horizontally centred
    assert alpha.getpixel((w // 2, h - margin_b + 2)) > 0


def test_shadow_offset_points_downwards(font):
    """A positive y offset must read as light from above: darker under the card than over it."""
    shadow = Shadow(enabled=True, blur=10, offset=Position(x=0, y=10))
    alpha = _alpha(compose(_shadow_guide(shadow), "Card", {}, font))
    w, h = alpha.size
    margin_l, margin_t, _, margin_b = shadow_margins(shadow)

    above = alpha.getpixel((w // 2, margin_t - 2))
    below = alpha.getpixel((w // 2, h - margin_b + 2))
    assert below > above


def test_shadow_does_not_bleed_through_translucent_card(font):
    """The card background is translucent; an unclipped shadow would darken it from behind."""
    shadow = Shadow(enabled=True, blur=12, color=(0, 0, 0, 255), offset=Position(x=0, y=6))
    plain = Image.open(io.BytesIO(compose(_shadow_guide(), "Card", {}, font))).convert("RGBA")
    shaded = Image.open(io.BytesIO(compose(_shadow_guide(shadow), "Card", {}, font))).convert("RGBA")
    margin_l, margin_t, _, _ = shadow_margins(shadow)

    # same point of the card in both renders, well inside the rounded corners
    inside = (CARD_W // 2, 30)
    assert shaded.getpixel((inside[0] + margin_l, inside[1] + margin_t)) == plain.getpixel(inside)


def test_negative_shadow_offset_does_not_clip(font):
    """Geometry must hold for a shadow cast up-left, not just down-right."""
    png = compose(
        _shadow_guide(Shadow(enabled=True, blur=6, offset=Position(x=-9, y=-9))), "Card", {}, font
    )
    alpha = _alpha(png)
    w, h = alpha.size
    assert max(alpha.getpixel((x, 0)) for x in range(w)) == 0
    assert max(alpha.getpixel((0, y)) for y in range(h)) == 0


def test_shadow_spread_shrunk_to_nothing_does_not_raise(font):
    shadow = Shadow(enabled=True, blur=4, spread=-CARD_W, offset=Position())
    assert compose(_shadow_guide(shadow), "Card", {}, font)[:8] == PNG_MAGIC


def test_zero_blur_shadow_is_a_hard_edged_offset_copy(font):
    shadow = Shadow(enabled=True, blur=0, offset=Position(x=0, y=10))
    assert shadow_margins(shadow) == (0, 0, 0, 10)
    assert compose(_shadow_guide(shadow), "Card", {}, font)[:8] == PNG_MAGIC


def test_load_guide_reads_shadow_block(tmp_path):
    data = {
        "version": "1.0",
        "canvas": {"width": 600},
        "background": {
            "color": [20, 20, 20, 200],
            "radius": 8,
            "padding": {"top": 10, "right": 10, "bottom": 10, "left": 10},
            "shadow": {
                "enabled": True,
                "color": [0, 0, 0, 150],
                "blur": 14,
                "spread": 2,
                "offset": {"x": 0, "y": 8},
            },
        },
        "assets": [],
        "text": {"font_path": "", "size": 16, "color": [255, 255, 255, 255], "offset": {"x": 0, "y": 0}},
    }
    guide_file = tmp_path / "guide.json"
    guide_file.write_text(json.dumps(data))
    shadow = load_guide(guide_file).background.shadow

    assert shadow.enabled is True
    assert (shadow.blur, shadow.spread, shadow.offset.y) == (14, 2, 8)


def test_load_guide_without_shadow_block_defaults_to_disabled(tmp_path):
    """Guides written before the feature existed must keep their original geometry."""
    data = {
        "version": "1.0",
        "canvas": {"width": 600},
        "background": {"color": [20, 20, 20, 200], "radius": 8},
        "assets": [],
        "text": {"font_path": "", "size": 16, "color": [255, 255, 255, 255], "offset": {"x": 0, "y": 0}},
    }
    guide_file = tmp_path / "guide.json"
    guide_file.write_text(json.dumps(data))
    assert load_guide(guide_file).background.shadow.enabled is False


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
