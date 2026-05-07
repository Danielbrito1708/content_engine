import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.blender_worker.db.engine import get_session
from src.blender_worker.db.models import Template
from src.blender_worker.schemas.template import TemplateCreate, TemplateResponse

router = APIRouter(prefix="/templates")


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
    result = await session.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template
