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
    story_scored: int
    weak_storytelling: int
    story_quality_unavailable: bool
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
    #: All ``None`` when the item was never judged (filtered out, or scored during
    #: an ``llm_service`` outage). Not the same as a weak story.
    has_hook: bool | None
    story_score: int | None
    #: ``weak_storytelling`` / ``no_hook`` / ``strong``. Filter on this to see what
    #: the model is rejecting before moving ``min_story_score``.
    story_tag: str | None
    hook_line: str | None
    story_reason: str | None
    status: SeenStatus
    skip_reason: str | None
    pipeline_run_id: uuid.UUID | None
    created_at: datetime
    comments: list[ItemCommentResponse] = []

    model_config = {"from_attributes": True}
