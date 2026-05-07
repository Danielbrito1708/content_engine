import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from src.blender_worker.image.composer import compose, load_font, load_guide
from src.blender_worker.schemas.image import ImageRenderRequest, ImageRenderResponse
from src.blender_worker.storage.client import get_s3_client
from src.core import settings

router = APIRouter(prefix="/images")


@router.post("/render", response_model=ImageRenderResponse, status_code=201)
async def render_image(body: ImageRenderRequest):
    root_dir = Path(settings.ROOT_DIR)
    guide_path = root_dir / "templates" / f"{body.template}.json"

    if not guide_path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{body.template}' not found")

    guide = load_guide(guide_path)
    font = load_font(guide, root_dir)

    s3 = get_s3_client()
    bucket = settings.CONFIG.storage.bucket

    asset_images: dict[str, bytes] = {}
    for spec in guide.assets:
        key = body.assets.get(spec.id, spec.minio_key)
        try:
            raw: bytes = await asyncio.to_thread(
                lambda _k=key: s3.get_object(Bucket=bucket, Key=_k)["Body"].read()
            )
            asset_images[spec.id] = raw
        except Exception:
            pass  # compose silently skips missing assets

    png_bytes = await asyncio.to_thread(compose, guide, body.text, asset_images, font)

    output_key = body.output_key or f"renders/{uuid.uuid4()}.png"
    await asyncio.to_thread(
        s3.put_object,
        Bucket=bucket,
        Key=output_key,
        Body=png_bytes,
        ContentType="image/png",
    )

    return ImageRenderResponse(output_key=output_key)
