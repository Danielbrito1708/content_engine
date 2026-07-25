import uuid
from datetime import datetime

from pydantic import BaseModel

from src.content_scout.db.models import SeenStatus


class ScoutRunResponse(BaseModel):
    fetched: int
    already_seen: int
    filtered: int
    unsafe: int
    submitted: int
    skipped_no_capacity: bool
    moderation_unavailable: bool
    active_runs: int
    submitted_ids: list[str]


class SeenItemResponse(BaseModel):
    id: uuid.UUID
    source: str
    external_id: str
    origin: str
    title: str
    url: str
    char_count: int
    status: SeenStatus
    skip_reason: str | None
    pipeline_run_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}
