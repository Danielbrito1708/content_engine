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


class Canvas(BaseModel):
    width: int = 800


class CommentGuide(BaseModel):
    version: str = "1.0"
    canvas: Canvas
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

def compose(
    guide: CommentGuide,
    text: str,
    asset_images: dict[str, bytes],
    font: ImageFont.FreeTypeFont,
    line_spacing: int = 4,
) -> bytes:
    """Compose a comment card and return raw PNG bytes.

    `guide.canvas.width` is the width of the *card*. When the guide enables a shadow the PNG comes
    back larger than that, with the card inset by `shadow_margins()` — see the docstring there.
    """
    pad = guide.background.padding
    card_w = guide.canvas.width

    text_x = pad.left + guide.text.offset.x
    text_max_w = card_w - text_x - pad.right

    block = measure(text, font, text_max_w, line_spacing)

    max_asset_h = max((a.size.height for a in guide.assets), default=0)
    content_h = max(max_asset_h, block.total_height)
    card_h = pad.top + content_h + pad.bottom

    margin_l, margin_t, margin_r, margin_b = shadow_margins(guide.background.shadow)
    canvas = Image.new(
        "RGBA",
        (margin_l + card_w + margin_r, margin_t + card_h + margin_b),
        (0, 0, 0, 0),
    )
    origin = (margin_l, margin_t)

    if guide.background.shadow.enabled:
        _draw_shadow(canvas, guide.background.shadow, origin, (card_w, card_h), guide.background.radius)

    _draw_rounded_rect(
        canvas, (card_w, card_h), guide.background.radius, guide.background.color, dest=origin
    )

    for spec in guide.assets:
        raw = asset_images.get(spec.id)
        if raw is None:
            continue
        asset_img = Image.open(io.BytesIO(raw)).convert("RGBA")
        asset_img = asset_img.resize((spec.size.width, spec.size.height), Image.LANCZOS)
        canvas.alpha_composite(
            asset_img,
            dest=(margin_l + pad.left + spec.position.x, margin_t + pad.top + spec.position.y),
        )

    draw = ImageDraw.Draw(canvas)
    y = margin_t + pad.top + guide.text.offset.y
    for line in block.lines:
        draw.text((margin_l + text_x, y), line, font=font, fill=guide.text.color)
        y += block.line_height + block.line_spacing

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
