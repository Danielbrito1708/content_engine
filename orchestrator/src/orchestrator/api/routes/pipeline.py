import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.notify import notify, short_id
from src.orchestrator.db.engine import get_session
from src.orchestrator.db.models import PipelineRun
from src.orchestrator.schemas.pipeline import PipelineCreate, PipelineResponse
from src.orchestrator.worker import run_pipeline

router = APIRouter(prefix="/pipeline")


@router.post("", response_model=PipelineResponse, status_code=201)
async def create_pipeline(
    body: PipelineCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    run = PipelineRun(
        raw_script=body.script,
        input_metadata=body.metadata or None,
        template_id=body.template_id,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    background_tasks.add_task(run_pipeline, run.id)

    notify(
        "Roteiro recebido — pipeline começou",
        icon="📥",
        run=short_id(run.id),
        origem=(run.input_metadata or {}).get("origin"),
        titulo=((run.input_metadata or {}).get("title") or "")[:100] or None,
        chars=len(run.raw_script),
    )

    result = await session.execute(
        select(PipelineRun).where(PipelineRun.id == run.id).options(selectinload(PipelineRun.parts))
    )
    return result.scalar_one()


@router.get("/{run_id}", response_model=PipelineResponse)
async def get_pipeline(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(PipelineRun).where(PipelineRun.id == run_id).options(selectinload(PipelineRun.parts))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return run


@router.get("", response_model=list[PipelineResponse])
async def list_pipelines(
    limit: int = 20,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(PipelineRun)
        .options(selectinload(PipelineRun.parts))
        .order_by(PipelineRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()
