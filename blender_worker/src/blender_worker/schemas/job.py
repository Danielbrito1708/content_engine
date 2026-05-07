import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from src.blender_worker.db.models import JobStatus


class JobCreate(BaseModel):
    video_id: uuid.UUID
    template_id: uuid.UUID
    params: dict[str, Any] | None = None


class JobResponse(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    template_id: uuid.UUID
    status: JobStatus
    output_key: str | None
    params: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
