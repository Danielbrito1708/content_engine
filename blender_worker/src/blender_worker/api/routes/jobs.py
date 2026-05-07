import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.blender_worker.db.engine import get_session
from src.blender_worker.db.models import Job, Template, Video
from src.blender_worker.schemas.job import JobCreate, JobResponse
from src.blender_worker.worker import render_job

router = APIRouter(prefix="/jobs")


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    body: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    video = await session.get(Video, body.video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    template = await session.get(Template, body.template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    job = Job(
        video_id=body.video_id,
        template_id=body.template_id,
        params=body.params,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    background_tasks.add_task(render_job, job.id)
    return job


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
