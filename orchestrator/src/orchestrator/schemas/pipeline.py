import uuid
from datetime import datetime

from pydantic import BaseModel

from src.orchestrator.db.models import PartStatus, PipelineStatus


class PipelineCreate(BaseModel):
    script: str
    metadata: dict = {}


class PartResponse(BaseModel):
    id: uuid.UUID
    part_number: int
    status: PartStatus
    audio_key: str | None
    video_key: str | None
    scheduled_at: datetime | None
    posted_at: datetime | None
    tiktok_video_id: str | None
    error: str | None

    model_config = {"from_attributes": True}


class PipelineResponse(BaseModel):
    id: uuid.UUID
    status: PipelineStatus
    parts_count: int
    hook: str | None
    hook_audio_key: str | None
    hook_srt_key: str | None
    classification: dict | None
    error: str | None
    parts: list[PartResponse]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
