from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from pydantic import BaseModel

from src.blender_worker.image.text import measure

# Practical support of Pillow's Gaussian kernel, in multiples of `blur`. Pillow takes the
# radius as a standard deviation, and ~3σ holds >99% of the kernel weight — anything past
# it is below one 8-bit alpha step, so this is the margin that keeps the blur from clipping.
BLUR_EXTENT = 3


# ── Guide schema ──────────────────────────────────────────────────────────────

class Position(BaseModel):
    x: int = 0
    y: int = 0


class Padding(BaseModel):
    top: int = 16
    right: int = 20
    bottom: int = 16
    left: int = 20


class Shadow(BaseModel):
    enabled: bool = False
    color: tuple[int, int, int, int] = (0, 0, 0, 140)
    blur: int = 12
    spread: int = 0
    offset: Position = Position(x=0, y=6)


class Background(BaseModel):
    color: tuple[int, int, int, int] = (30, 30, 30, 220)
    radius: int = 16
    padding: Padding = Padding()
    shadow: Shadow = Shadow()


class Size(BaseModel):
    width: int
    height: int


class AssetSpec(BaseModel):
    id: str
    minio_key: str
    size: Size
    position: Position = Position()


class TextSpec(BaseModel):
    font_path: str
    size: int = 18
    color: tuple[int, int, int, int] = (255, 255, 255, 255)
    offset: Position = Position()
    line_spacing: int = 4


class Canvas(BaseModel):
    """The output PNG. `width` is fixed; height is derived from the card's content."""

    width: int = 1080
    supersample: int = 1


class Card(BaseModel):
    """The card itself, placed inside the canvas.

    `offset.x` is the card's left inset — smaller than `(canvas.width - card.width) / 2`
    shifts the card left of centre, which is what keeps it clear of TikTok's right-hand
    action rail. `gap` is the vertical space between the asset row and the text below it.
    """

    width: int = 880
    offset: Position = Position(x=40, y=0)
    gap: int = 16


class CommentGuide(BaseModel):
    version: str = "2.0"
    canvas: Canvas
    card: Card = Card()
    background: Background
    assets: list[AssetSpec] = []
    text: TextSpec


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_guide(path: Path) -> CommentGuide:
    return CommentGuide.model_validate_json(path.read_text())


def load_font(guide: CommentGuide, root_dir: Path) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(root_dir / guide.text.font_path), guide.text.size)


def _rounded_rect(
    size: tuple[int, int],
    radius: int,
    color: tuple[int, int, int, int],
    scale: int = 4,
) -> Image.Image:
    """A rounded rectangle drawn at `scale`× and downsampled, so the corners are antialiased."""
    w, h = size
    big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        [0, 0, w * scale - 1, h * scale - 1],
        radius=radius * scale,
        fill=color,
    )
    return big.resize((w, h), Image.LANCZOS)


def _draw_rounded_rect(
    img: Image.Image,
    size: tuple[int, int],
    radius: int,
    color: tuple[int, int, int, int],
    dest: tuple[int, int] = (0, 0),
    scale: int = 4,
) -> None:
    img.alpha_composite(_rounded_rect(size, radius, color, scale), dest=dest)


# ── Shadow ────────────────────────────────────────────────────────────────────

def shadow_margins(shadow: Shadow) -> tuple[int, int, int, int]:
    """Transparent padding `(left, top, right, bottom)` the blurred shadow needs around the card.

    A drop shadow is blurred and offset, so it reaches outside the card's own box. The canvas has
    to grow by exactly this much or the blur gets clipped into a hard edge along the border.
    """
    if not shadow.enabled:
        return (0, 0, 0, 0)

    reach = shadow.blur * BLUR_EXTENT + shadow.spread
    return (
        max(0, reach - shadow.offset.x),
        max(0, reach - shadow.offset.y),
        max(0, reach + shadow.offset.x),
        max(0, reach + shadow.offset.y),
    )


def _draw_shadow(
    canvas: Image.Image,
    shadow: Shadow,
    card_pos: tuple[int, int],
    card_size: tuple[int, int],
    radius: int,
) -> None:
    """Composite a blurred, card-shaped shadow onto `canvas`, clipped to outside the card."""
    card_w, card_h = card_size
    rect_w = card_w + shadow.spread * 2
    rect_h = card_h + shadow.spread * 2
    if rect_w <= 0 or rect_h <= 0:  # a spread that shrinks the shadow away entirely
        return

    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.alpha_composite(
        _rounded_rect((rect_w, rect_h), max(0, radius + shadow.spread), shadow.color),
        dest=(
            card_pos[0] + shadow.offset.x - shadow.spread,
            card_pos[1] + shadow.offset.y - shadow.spread,
        ),
    )
    if shadow.blur > 0:
        layer = layer.filter(ImageFilter.GaussianBlur(shadow.blur))

    # Clip the shadow to outside the card's silhouette. The card background is translucent
    # (alpha 230 in the shipped template), so an unclipped shadow shows *through* it and
    # darkens the card unevenly — brightest where the offset points. CSS `box-shadow` clips
    # the same way, which is what the card is modelled on.
    occluder = Image.new("L", canvas.size, 0)
    occluder.paste(_rounded_rect(card_size, radius, (255, 255, 255, 255)).getchannel("A"), card_pos)
    layer.putalpha(ImageChops.subtract(layer.getchannel("A"), occluder))

    canvas.alpha_composite(layer)


# ── Compositor ────────────────────────────────────────────────────────────────

def check_card_fits(guide: CommentGuide) -> None:
    """Raise if the card plus its shadow does not fit the canvas width.

    The canvas is a fixed width, so unlike the vertical axis there is no room to grow into:
    a card placed too close to either edge silently clips the shadow's falloff into a hard
    line. That is a template authoring mistake, caught once here rather than per render.
    """
    margin_l, _, margin_r, _ = shadow_margins(guide.background.shadow)
    left = guide.card.offset.x - margin_l
    right = guide.canvas.width - (guide.card.offset.x + guide.card.width + margin_r)

    if left < 0 or right < 0:
        raise ValueError(
            f"card ({guide.card.width}px at x={guide.card.offset.x}) plus its shadow "
            f"({margin_l}px left, {margin_r}px right) does not fit a {guide.canvas.width}px "
            f"canvas: overflows by {abs(min(0, left))}px left, {abs(min(0, right))}px right"
        )


def compose(
    guide: CommentGuide,
    text: str,
    asset_images: dict[str, bytes],
    font: ImageFont.FreeTypeFont,
    line_spacing: int | None = None,
) -> bytes:
    """Compose a comment card and return raw PNG bytes.

    The PNG is always `guide.canvas.width` wide; only the height varies, with the text. The card
    is narrower than the canvas and sits at `guide.card.offset`, so the transparent remainder is
    part of the frame — the image is meant to be dropped onto the video at full width.

    `line_spacing` overrides `guide.text.line_spacing` when given.
    """
    check_card_fits(guide)

    scale = max(1, guide.canvas.supersample)
    pad = guide.background.padding
    spacing = guide.text.line_spacing if line_spacing is None else line_spacing

    # Everything below is in device pixels — logical units times `scale`. The whole card is
    # drawn oversized and downsampled once at the end, which antialiases the glyph edges and
    # the rounded corners together instead of each on its own terms.
    if scale > 1:
        font = font.font_variant(size=font.size * scale)

    card_w = guide.card.width * scale
    text_x = (pad.left + guide.text.offset.x) * scale
    block = measure(text, font, card_w - text_x - pad.right * scale, spacing * scale)

    row_h = max((a.size.height for a in guide.assets), default=0) * scale
    gap = guide.card.gap * scale if guide.assets else 0
    card_h = (pad.top + pad.bottom) * scale + row_h + gap + block.total_height

    margin_l, margin_t, margin_r, margin_b = (m * scale for m in shadow_margins(guide.background.shadow))
    canvas_w = guide.canvas.width * scale
    card_x = guide.card.offset.x * scale
    card_y = margin_t + guide.card.offset.y * scale
    canvas_h = card_y + card_h + margin_b

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    origin = (card_x, card_y)
    radius = guide.background.radius * scale

    if guide.background.shadow.enabled:
        scaled_shadow = guide.background.shadow.model_copy(
            update={
                "blur": guide.background.shadow.blur * scale,
                "spread": guide.background.shadow.spread * scale,
                "offset": Position(
                    x=guide.background.shadow.offset.x * scale,
                    y=guide.background.shadow.offset.y * scale,
                ),
            }
        )
        _draw_shadow(canvas, scaled_shadow, origin, (card_w, card_h), radius)

    _draw_rounded_rect(canvas, (card_w, card_h), radius, guide.background.color, dest=origin)

    # Asset row, on top of the text rather than beside it.
    for spec in guide.assets:
        raw = asset_images.get(spec.id)
        if raw is None:
            continue
        asset_img = Image.open(io.BytesIO(raw)).convert("RGBA")
        asset_img = asset_img.resize((spec.size.width * scale, spec.size.height * scale), Image.LANCZOS)
        canvas.alpha_composite(
            asset_img,
            dest=(
                card_x + (pad.left + spec.position.x) * scale,
                card_y + (pad.top + spec.position.y) * scale,
            ),
        )

    draw = ImageDraw.Draw(canvas)
    y = card_y + pad.top * scale + row_h + gap + guide.text.offset.y * scale
    for line in block.lines:
        draw.text((card_x + text_x, y), line, font=font, fill=guide.text.color)
        y += block.line_height + block.line_spacing

    if scale > 1:
        canvas = canvas.resize(
            (guide.canvas.width, -(-canvas_h // scale)),  # ceil, so the last text row is never cropped
            Image.LANCZOS,
        )

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
