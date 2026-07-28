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
    comments_fetched: int
    #: True when a cycle was already in flight, so this call did nothing.
    already_running: bool = False


class ItemCommentResponse(BaseModel):
    external_id: str
    author: str | None
    text: str
    position: int
    published: str | None

    model_config = {"from_attributes": True}


class SeenItemResponse(BaseModel):
    id: uuid.UUID
    source: str
    external_id: str
    origin: str
    title: str
    url: str
    char_count: int
    author: str | None
    #: ``None`` means the item was never enriched, not that it drew no replies.
    comment_count: int | None
    status: SeenStatus
    skip_reason: str | None
    pipeline_run_id: uuid.UUID | None
    created_at: datetime
    comments: list[ItemCommentResponse] = []

    model_config = {"from_attributes": True}
