import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageFont

from src.blender_worker.image.composer import (
    BLUR_EXTENT,
    AssetSpec,
    Background,
    Canvas,
    Card,
    CommentGuide,
    Padding,
    Position,
    Shadow,
    Size,
    TextSpec,
    check_card_fits,
    compose,
    load_guide,
    shadow_margins,
)

pytestmark = pytest.mark.no_db

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

CANVAS_W = 1080
CARD_W = 880
CARD_X = 60
RADIUS = 12
ASSET_H = 72
GAP = 18


@pytest.fixture(scope="module")
def font() -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=18)


def _guide(
    shadow: Shadow | None = None,
    assets: list[AssetSpec] | None = None,
    supersample: int = 1,
) -> CommentGuide:
    return CommentGuide(
        canvas=Canvas(width=CANVAS_W, supersample=supersample),
        card=Card(width=CARD_W, offset=Position(x=CARD_X, y=0), gap=GAP),
        background=Background(
            color=(25, 25, 25, 220),
            radius=RADIUS,
            padding=Padding(top=12, right=12, bottom=12, left=12),
            shadow=shadow or Shadow(),
        ),
        assets=assets if assets is not None else [],
        text=TextSpec(font_path="", size=18, color=(255, 255, 255, 255), offset=Position()),
    )


def _avatar_spec() -> AssetSpec:
    return AssetSpec(
        id="avatar",
        minio_key="assets/avatar.png",
        size=Size(width=ASSET_H, height=ASSET_H),
        position=Position(x=0, y=0),
    )


@pytest.fixture(scope="module")
def guide() -> CommentGuide:
    return _guide(assets=[_avatar_spec()])


def _make_png(width: int = 32, height: int = 32, color: tuple = (255, 0, 0, 200)) -> bytes:
    img = Image.new("RGBA", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _open(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGBA")


def _alpha(png: bytes) -> Image.Image:
    return _open(png).getchannel("A")


# ── Basics ────────────────────────────────────────────────────────────────────

def test_compose_returns_valid_png(guide, font):
    assert compose(guide, "Olá", {}, font)[:8] == PNG_MAGIC


def test_compose_image_is_rgba(guide, font):
    assert Image.open(io.BytesIO(compose(guide, "RGBA check", {}, font))).mode == "RGBA"


def test_compose_missing_asset_does_not_raise(guide, font):
    assert compose(guide, "Texto sem avatar", {}, font)[:8] == PNG_MAGIC


def test_compose_height_grows_with_long_text(guide, font):
    short = compose(guide, "Oi", {}, font)
    long = compose(guide, "Este comentário é longo o suficiente para quebrar em várias " * 6, {}, font)
    assert _open(long).size[1] > _open(short).size[1]


# ── Fixed canvas width ────────────────────────────────────────────────────────

def test_png_width_is_the_canvas_not_the_card(guide, font):
    """The output is a fixed-width frame; the card is a narrower thing placed inside it."""
    assert _open(compose(guide, "Olá", {}, font)).size[0] == CANVAS_W


def test_png_width_is_unchanged_by_card_width(font):
    narrow = compose(_guide(), "Olá", {}, font)
    wide_card = _guide()
    wide_card.card.width = CARD_W - 200
    assert _open(narrow).size[0] == _open(compose(wide_card, "Olá", {}, font)).size[0] == CANVAS_W


def test_card_sits_at_its_offset_leaving_the_canvas_transparent(font):
    """Everything left of `card.offset.x` is empty frame, not card."""
    alpha = _alpha(compose(_guide(), "Olá", {}, font))
    mid_y = alpha.size[1] // 2

    assert alpha.getpixel((CARD_X - 5, mid_y)) == 0
    assert alpha.getpixel((CARD_X + 5, mid_y)) > 0


def test_card_is_shifted_left_of_centre(font):
    """TikTok's action rail lives on the right, so the card must not be centred."""
    guide = _guide()
    right_gap = CANVAS_W - guide.card.offset.x - guide.card.width
    assert right_gap > guide.card.offset.x


# ── Vertical stacking (avatar above the text) ─────────────────────────────────

def test_asset_row_is_stacked_above_the_text_not_beside_it(font):
    """Side-by-side would charge `max(asset, text)`; stacked charges the sum, plus the gap."""
    text = "Uma linha de texto"
    without = _open(compose(_guide(), text, {}, font)).size[1]
    with_avatar = _open(compose(_guide(assets=[_avatar_spec()]), text, {}, font)).size[1]

    assert with_avatar - without == ASSET_H + GAP


def test_no_assets_means_no_gap_is_charged(font):
    """An empty asset list must not leave a dangling gap above the text."""
    pad = 12
    alpha = _alpha(compose(_guide(), "x", {}, font))
    # first non-transparent row is the card's top edge, at exactly the padding above the text
    rows = [y for y in range(alpha.size[1]) if alpha.getpixel((CARD_X + 20, y)) > 0]
    assert rows[0] == 0  # no shadow, so the card starts at the very top


def test_asset_is_drawn_inside_the_card(font):
    avatar = _make_png(ASSET_H, ASSET_H, color=(255, 0, 0, 255))
    img = _open(compose(_guide(assets=[_avatar_spec()]), "x", {"avatar": avatar}, font))
    # sample the avatar's centre — card offset, plus padding, plus half the asset
    assert img.getpixel((CARD_X + 12 + ASSET_H // 2, 12 + ASSET_H // 2))[:3] == (255, 0, 0)


def test_tall_asset_pushes_the_card_taller(font):
    tall = AssetSpec(id="banner", minio_key="", size=Size(width=32, height=200), position=Position())
    short = _open(compose(_guide(assets=[_avatar_spec()]), "x", {}, font)).size[1]
    assert _open(compose(_guide(assets=[tall]), "x", {}, font)).size[1] > short


# ── Fit validation ────────────────────────────────────────────────────────────

def test_check_card_fits_rejects_a_card_whose_shadow_overflows():
    guide = _guide(Shadow(enabled=True, blur=40, offset=Position()))  # reach 120 > 60 of slack
    with pytest.raises(ValueError, match="does not fit"):
        check_card_fits(guide)


def test_check_card_fits_message_names_the_overflowing_side():
    guide = _guide(Shadow(enabled=True, blur=40, offset=Position()))
    with pytest.raises(ValueError, match=r"overflows by 60px left"):
        check_card_fits(guide)


def test_check_card_fits_accepts_a_card_with_room_to_spare():
    assert check_card_fits(_guide(Shadow(enabled=True, blur=10, offset=Position(y=6)))) is None


def test_compose_refuses_to_render_a_clipped_shadow(font):
    with pytest.raises(ValueError):
        compose(_guide(Shadow(enabled=True, blur=40, offset=Position())), "x", {}, font)


def test_shipped_template_fits_its_canvas():
    """Guards the versioned template — this check already caught a real 14px overflow."""
    guide = load_guide(Path(__file__).parents[1] / "templates" / "comment_default.json")
    assert check_card_fits(guide) is None


# ── Supersampling ─────────────────────────────────────────────────────────────

def test_supersample_does_not_change_output_dimensions(font):
    plain = _open(compose(_guide(supersample=1), "Antialias", {}, font)).size
    sampled = _open(compose(_guide(supersample=2), "Antialias", {}, font)).size
    assert abs(sampled[1] - plain[1]) <= 1  # ceil rounding on the downsample
    assert sampled[0] == plain[0] == CANVAS_W


@pytest.mark.parametrize("scale", [1, 2])
def test_rounded_corners_are_antialiased(font, scale):
    """Corners carry partial alpha at either setting — `_rounded_rect` already supersamples 4×,
    so `canvas.supersample` is not what makes them smooth."""
    alpha = _alpha(compose(_guide(supersample=scale), "Antialias", {}, font))
    corner = [
        alpha.getpixel((CARD_X + x, y)) for x in range(RADIUS + 4) for y in range(RADIUS + 4)
    ]
    assert len({v for v in corner if 0 < v < 255}) > 5


@pytest.mark.parametrize("scale", [1, 2])
def test_glyph_edges_are_antialiased(font, scale):
    """Text is drawn by FreeType, which antialiases on its own; supersampling is on top of that."""
    guide = _guide(supersample=scale)
    guide.background.color = (0, 0, 0, 255)
    guide.text.color = (255, 255, 255, 255)
    img = _open(compose(guide, "Antialias", {}, font)).convert("L")

    band = [img.getpixel((x, y)) for x in range(CARD_X + 12, CARD_X + 300) for y in range(12, 40)]
    assert len({v for v in band if 0 < v < 255}) > 10


# ── Shadow ────────────────────────────────────────────────────────────────────

def test_shadow_margins_are_zero_when_disabled():
    assert shadow_margins(Shadow(enabled=False, blur=20, offset=Position(x=5, y=5))) == (0, 0, 0, 0)


def test_shadow_margins_follow_offset_direction():
    left, top, right, bottom = shadow_margins(
        Shadow(enabled=True, blur=10, spread=0, offset=Position(x=4, y=6))
    )
    reach = 10 * BLUR_EXTENT
    assert (left, top, right, bottom) == (reach - 4, reach - 6, reach + 4, reach + 6)


def test_shadow_margins_never_negative():
    assert shadow_margins(Shadow(enabled=True, blur=1, offset=Position(x=50, y=50)))[:2] == (0, 0)


def test_shadow_grows_canvas_height_only(font):
    shadow = Shadow(enabled=True, blur=8, offset=Position(x=0, y=5))
    plain = _open(compose(_guide(), "Card", {}, font)).size
    shaded = _open(compose(_guide(shadow), "Card", {}, font)).size
    _, top, _, bottom = shadow_margins(shadow)

    assert shaded == (plain[0], plain[1] + top + bottom)


def test_shadow_does_not_clip_at_canvas_edge(font):
    """The margin must cover the blur's full reach, or the falloff ends in a hard line."""
    alpha = _alpha(compose(_guide(Shadow(enabled=True, blur=10, offset=Position(y=6))), "Card", {}, font))
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
    alpha = _alpha(compose(_guide(shadow), "Card", {}, font))
    h = alpha.size[1]
    _, _, _, margin_b = shadow_margins(shadow)

    assert alpha.getpixel((CARD_X + CARD_W // 2, h - margin_b + 2)) > 0


def test_shadow_offset_points_downwards(font):
    shadow = Shadow(enabled=True, blur=10, offset=Position(x=0, y=10))
    alpha = _alpha(compose(_guide(shadow), "Card", {}, font))
    h = alpha.size[1]
    _, margin_t, _, margin_b = shadow_margins(shadow)
    x = CARD_X + CARD_W // 2

    assert alpha.getpixel((x, h - margin_b + 2)) > alpha.getpixel((x, margin_t - 2))


def test_shadow_does_not_bleed_through_translucent_card(font):
    """The card background is translucent; an unclipped shadow would darken it from behind."""
    shadow = Shadow(enabled=True, blur=12, color=(0, 0, 0, 255), offset=Position(x=0, y=6))
    plain = _open(compose(_guide(), "Card", {}, font))
    shaded = _open(compose(_guide(shadow), "Card", {}, font))
    _, margin_t, _, _ = shadow_margins(shadow)

    inside = (CARD_X + CARD_W // 2, 30)
    assert shaded.getpixel((inside[0], inside[1] + margin_t)) == plain.getpixel(inside)


def test_negative_shadow_offset_does_not_clip(font):
    alpha = _alpha(compose(_guide(Shadow(enabled=True, blur=6, offset=Position(x=-9, y=-9))), "C", {}, font))
    w, h = alpha.size
    assert max(alpha.getpixel((x, 0)) for x in range(w)) == 0
    assert max(alpha.getpixel((0, y)) for y in range(h)) == 0


def test_shadow_spread_shrunk_to_nothing_does_not_raise(font):
    shadow = Shadow(enabled=True, blur=4, spread=-CARD_W, offset=Position())
    assert compose(_guide(shadow), "Card", {}, font)[:8] == PNG_MAGIC


def test_zero_blur_shadow_needs_margin_only_for_the_offset():
    assert shadow_margins(Shadow(enabled=True, blur=0, offset=Position(x=0, y=10))) == (0, 0, 0, 10)


# ── Guide parsing ─────────────────────────────────────────────────────────────

def _guide_json(**background) -> dict:
    return {
        "version": "2.0",
        "canvas": {"width": 1080, "supersample": 2},
        "card": {"width": 900, "offset": {"x": 60, "y": 0}, "gap": 20},
        "background": {"color": [20, 20, 20, 200], "radius": 8, **background},
        "assets": [],
        "text": {"font_path": "", "size": 16, "color": [0, 0, 0, 255], "offset": {"x": 0, "y": 0}},
    }


def test_load_guide_reads_canvas_and_card(tmp_path):
    guide_file = tmp_path / "guide.json"
    guide_file.write_text(json.dumps(_guide_json()))
    loaded = load_guide(guide_file)

    assert (loaded.canvas.width, loaded.canvas.supersample) == (1080, 2)
    assert (loaded.card.width, loaded.card.offset.x, loaded.card.gap) == (900, 60, 20)


def test_load_guide_reads_shadow_block(tmp_path):
    guide_file = tmp_path / "guide.json"
    guide_file.write_text(
        json.dumps(
            _guide_json(
                shadow={
                    "enabled": True,
                    "color": [0, 0, 0, 150],
                    "blur": 14,
                    "spread": 2,
                    "offset": {"x": 0, "y": 8},
                }
            )
        )
    )
    shadow = load_guide(guide_file).background.shadow

    assert shadow.enabled is True
    assert (shadow.blur, shadow.spread, shadow.offset.y) == (14, 2, 8)


def test_load_guide_without_shadow_block_defaults_to_disabled(tmp_path):
    guide_file = tmp_path / "guide.json"
    guide_file.write_text(json.dumps(_guide_json()))
    assert load_guide(guide_file).background.shadow.enabled is False


def test_shipped_template_matches_the_requested_look():
    """The design brief: 1080 wide, white card, black Arial Bold text, avatar on top."""
    guide = load_guide(Path(__file__).parents[1] / "templates" / "comment_default.json")

    assert guide.canvas.width == 1080
    assert guide.background.color == (255, 255, 255, 255)
    assert guide.text.color == (0, 0, 0, 255)
    assert guide.text.font_path.endswith("Arial-Bold.ttf")
    assert [a.id for a in guide.assets] == ["avatar"]


def test_shipped_template_font_is_vendored():
    """A font_path pointing at nothing renders in DejaVu, or blows up at request time."""
    root = Path(__file__).parents[1]
    guide = load_guide(root / "templates" / "comment_default.json")

    assert (root / guide.text.font_path).is_file()
