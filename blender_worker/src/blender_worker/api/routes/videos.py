import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.blender_worker.db.engine import get_session
from src.blender_worker.db.models import Video
from src.blender_worker.schemas.video import VideoCreate, VideoResponse

router = APIRouter(prefix="/videos")


@router.post("", response_model=VideoResponse, status_code=201)
async def create_video(
    body: VideoCreate,
    session: AsyncSession = Depends(get_session),
):
    video = Video(**body.model_dump())
    session.add(video)
    await session.commit()
    await session.refresh(video)
    return video


@router.get("/{video_id}", response_model=VideoResponse)
async def get_video(
    video_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Video).where(Video.id == video_id))
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video
