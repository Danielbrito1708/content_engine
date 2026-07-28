import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class VideoCreate(BaseModel):
    video_file_key: str
    music_key: str
    voice_key: str
    subtitle_key: str
    #: Intro (card + gancho). Opcionais e independentes: um vídeo sem eles
    #: renderiza como antes de a intro existir.
    card_key: str | None = None
    hook_voice_key: str | None = None
    video_metadata: dict[str, Any] | None = None


class VideoResponse(BaseModel):
    id: uuid.UUID
    video_file_key: str
    music_key: str
    voice_key: str
    subtitle_key: str
    card_key: str | None
    hook_voice_key: str | None
    video_metadata: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}
