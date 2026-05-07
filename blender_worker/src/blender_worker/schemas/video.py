import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class VideoCreate(BaseModel):
    video_file_key: str
    music_key: str
    voice_key: str
    subtitle_key: str
    video_metadata: dict[str, Any] | None = None


class VideoResponse(BaseModel):
    id: uuid.UUID
    video_file_key: str
    music_key: str
    voice_key: str
    subtitle_key: str
    video_metadata: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}
