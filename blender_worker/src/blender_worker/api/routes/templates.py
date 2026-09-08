import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.blender_worker.db.engine import get_session
from src.blender_worker.db.models import Template
from src.blender_worker.schemas.template import TemplateCreate, TemplateResponse, TemplateUpdate
from src.blender_worker.storage.client import download_bytes
from src.core import settings

router = APIRouter(prefix="/templates")


async def _get_or_404(template_id: uuid.UUID, session: AsyncSession) -> Template:
    result = await session.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.post("", response_model=TemplateResponse, status_code=201)
async def create_template(
    body: TemplateCreate,
    session: AsyncSession = Depends(get_session),
):
    template = Template(**body.model_dump())
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    return await _get_or_404(template_id, session)


@router.patch("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    body: TemplateUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Updates only the fields passed. Exists so retrofitting `yaml_key` onto
    the existing production row (the VSEL cutover, 08/09/2026) doesn't
    require creating a second `Template` and repointing every reference to
    it — see `TemplateUpdate`'s docstring."""
    template = await _get_or_404(template_id, session)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(template, field, value)
    await session.commit()
    await session.refresh(template)
    return template


@router.get("/{template_id}/config")
async def get_template_config(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The parsed `template.json`, so upstream services can read it before render time."""
    template = await _get_or_404(template_id, session)

    try:
        raw = await download_bytes(settings.CONFIG.storage.bucket, template.json_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch template config: {exc}")

    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Template config is not valid JSON: {exc}")

    if not isinstance(config, dict):
        raise HTTPException(status_code=502, detail="Template config must be a JSON object")

    return config
