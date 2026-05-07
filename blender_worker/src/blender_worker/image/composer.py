from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

from src.blender_worker.image.text import measure


# ── Guide schema ──────────────────────────────────────────────────────────────

class Padding(BaseModel):
    top: int = 16
    right: int = 20
    bottom: int = 16
    left: int = 20


class Background(BaseModel):
    color: tuple[int, int, int, int] = (30, 30, 30, 220)
    radius: int = 16
    padding: Padding = Padding()


class Size(BaseModel):
    width: int
    height: int


class Position(BaseModel):
    x: int = 0
    y: int = 0


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


def _draw_rounded_rect(
    img: Image.Image,
    size: tuple[int, int],
    radius: int,
    color: tuple[int, int, int, int],
    scale: int = 4,
) -> None:
    w, h = size
    big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        [0, 0, w * scale - 1, h * scale - 1],
        radius=radius * scale,
        fill=color,
    )
    img.alpha_composite(big.resize((w, h), Image.LANCZOS), dest=(0, 0))


# ── Compositor ────────────────────────────────────────────────────────────────

def compose(
    guide: CommentGuide,
    text: str,
    asset_images: dict[str, bytes],
    font: ImageFont.FreeTypeFont,
    line_spacing: int = 4,
) -> bytes:
    """Compose a comment card and return raw PNG bytes."""
    pad = guide.background.padding
    width = guide.canvas.width

    text_x = pad.left + guide.text.offset.x
    text_max_w = width - text_x - pad.right

    block = measure(text, font, text_max_w, line_spacing)

    max_asset_h = max((a.size.height for a in guide.assets), default=0)
    content_h = max(max_asset_h, block.total_height)
    canvas_h = pad.top + content_h + pad.bottom

    canvas = Image.new("RGBA", (width, canvas_h), (0, 0, 0, 0))

    _draw_rounded_rect(canvas, (width, canvas_h), guide.background.radius, guide.background.color)

    for spec in guide.assets:
        raw = asset_images.get(spec.id)
        if raw is None:
            continue
        asset_img = Image.open(io.BytesIO(raw)).convert("RGBA")
        asset_img = asset_img.resize((spec.size.width, spec.size.height), Image.LANCZOS)
        canvas.alpha_composite(asset_img, dest=(pad.left + spec.position.x, pad.top + spec.position.y))

    draw = ImageDraw.Draw(canvas)
    y = pad.top + guide.text.offset.y
    for line in block.lines:
        draw.text((text_x, y), line, font=font, fill=guide.text.color)
        y += block.line_height + block.line_spacing

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
