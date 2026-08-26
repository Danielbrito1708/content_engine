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
    #: Judged candidates under ``min_outrage_score``, and those with a clear
    #: villain. Counters only — neither one rejects anything.
    low_outrage: int = 0
    with_villain: int = 0
    story_quality_unavailable: bool
    #: Origin whose all-time archive was paged this cycle, ``None`` when none was
    #: due. A sweep costs one rate-limit window, so at most one runs per cycle.
    archive_swept: str | None = None
    archive_fetched: int = 0
    #: The sweep ran off the end of the listing and wrapped back to the top.
    archive_wrapped: bool = False
    #: Candidates skipped because the same story was already seen under another id.
    duplicate_story: int = 0
    #: Candidates that were judged and recorded with their story score, then
    #: dropped for being too long to produce. Included in ``filtered`` too.
    too_long: int = 0
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
    #: Outrage potential and villain flag. ``None`` = not judged, which includes
    #: every row older than migration 005.
    outrage_score: int | None = None
    has_villain: bool | None = None
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
