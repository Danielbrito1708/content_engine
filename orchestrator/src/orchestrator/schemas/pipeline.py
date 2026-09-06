import uuid
from datetime import datetime

from pydantic import BaseModel

from src.orchestrator.db.models import PartStatus, PipelineStatus


class PipelineCreate(BaseModel):
    script: str
    metadata: dict = {}
    #: Sobrescreve o template default só para este run. `None` preserva o
    #: comportamento atual (BLENDER_TEMPLATE_ID). Ver docs/vision.md →
    #: "Escolher o template por vídeo, não só pela conta inteira".
    template_id: uuid.UUID | None = None
    #: Conta de publicação dona deste run. `None` publica na conta default
    #: (credenciais globais do tiktok_poster) — comportamento de sempre. Ver
    #: docs/multi_account.md, Fase 1.
    account_id: uuid.UUID | None = None


class PartResponse(BaseModel):
    id: uuid.UUID
    part_number: int
    status: PartStatus
    audio_key: str | None
    video_key: str | None
    scheduled_at: datetime | None
    posted_at: datetime | None
    tiktok_video_id: str | None
    youtube_video_id: str | None
    error: str | None

    model_config = {"from_attributes": True}


class PipelineResponse(BaseModel):
    id: uuid.UUID
    status: PipelineStatus
    parts_count: int
    hook: str | None
    hook_audio_key: str | None
    hook_srt_key: str | None
    card_key: str | None
    narrator_gender: str | None
    youtube_title: str | None
    mood: str | None
    music_key: str | None
    template_id: uuid.UUID | None
    tts_voice: str | None
    account_id: uuid.UUID | None
    classification: dict | None
    error: str | None
    parts: list[PartResponse]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AccountCreate(BaseModel):
    slug: str
    template_id: uuid.UUID | None = None
    notes: str | None = None


class AccountResponse(BaseModel):
    id: uuid.UUID
    slug: str
    status: str
    template_id: uuid.UUID | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
