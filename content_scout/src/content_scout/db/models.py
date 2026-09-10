import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SeenStatus(str, PyEnum):
    submitted = "submitted"
    filtered = "filtered"
    failed = "failed"


class SeenItem(Base):
    """One row per candidate the scout has ever evaluated.

    Doubles as the dedup key (``external_id`` is unique) and as an audit trail:
    filtered items are recorded with the reason, so the filter thresholds can be
    tuned against real data instead of guesswork.
    """

    __tablename__ = "seen_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    origin: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Fingerprint of the body, for catching the same *story* posted again under a
    #: different id — which ``external_id`` cannot see. Indexed but deliberately
    #: **not** unique: a repost has to be recorded with its own audit row saying it
    #: was skipped, and a unique constraint would reject that row instead.
    #: ``None`` on every row written before the archive sweep existed — bodies are
    #: not stored, so the backlog cannot be fingerprinted retroactively.
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: Comments the source reported, ``None`` when they were never fetched.
    #: Enrichment costs a rate-limit window per item, so only published
    #: candidates carry it — ``None`` means "not looked at", not "zero replies".
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Story-quality judgement. All four are ``None`` when the candidate was never
    #: scored — filtered items are not, and neither is anything in a cycle where
    #: ``llm_service`` was unreachable. ``NULL`` means "not judged", which is not
    #: the same claim as a low score.
    has_hook: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    story_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Outrage potential, 0–10, and whether the model found someone plainly in the
    #: wrong. Indignation is what the channel selects for, so these are what the
    #: ordering actually runs on — kept raw next to ``story_score`` so the weight
    #: between the two can be re-derived against rows that were already published.
    #: ``NULL`` on every row written before migration 005, and on any row judged by
    #: a model that did not answer the field.
    outrage_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_villain: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: ``weak_storytelling`` / ``no_hook`` / ``strong`` — derived from
    #: ``story_score`` against a configurable threshold, stored so the audit trail
    #: reads without re-deriving it and so the label can be forwarded downstream.
    story_tag: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The line the model read as the hook, when it found one. Shows *what* was
    #: rewarded, which is what makes the threshold tunable against real data.
    hook_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[SeenStatus] = mapped_column(Enum(SeenStatus), nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: Publication account this candidate's run was sent to, mirroring the
    #: orchestrator's ``PipelineRun.account_id``. ``None`` is the implicit
    #: default account — the same meaning as on that side. Audit only: the
    #: round-robin picker itself reads recent history straight from this
    #: column (see ``scout.py::_submitted_per_account``), never this row in
    #: isolation. Every row before migration 006 is NULL, same convention as
    #: every other multi-account column added after the fact.
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    comments: Mapped[list["ItemComment"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", lazy="selectin"
    )


class ArchiveCursor(Base):
    """How far a historical sweep has read into one subreddit's all-time top.

    The weekly feed renews itself; ``top?t=all`` does not. Re-requesting it every
    cycle returns the *same* fifteen posts forever, all of them already in
    ``seen_items`` after the first pass — a rate-limit window spent to learn
    nothing. Reddit's Atom feeds accept ``?count=&after=``, verified live to
    return a page with zero overlap, so the sweep pages forward instead and this
    row remembers where it stopped.

    ``last_swept_at`` is also the cadence: the sweep is due when it is older than
    the configured interval. Keeping the schedule in the database rather than a
    process counter is what makes it survive a restart — a counter would reset on
    every deploy and re-sweep immediately.
    """

    __tablename__ = "archive_cursors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    #: ``r/{sub}`` — the same shape as ``SeenItem.origin``, so the two join by eye.
    origin: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    #: Reddit fullname of the last post read. ``None`` means "start from the top",
    #: which is both the initial state and where an exhausted sweep wraps back to.
    after_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Pages read since the cursor last wrapped. Purely diagnostic — it answers
    #: "is this sub still yielding?" without replaying the audit trail.
    pages_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_swept_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ItemComment(Base):
    """One stored reaction to a ``SeenItem``.

    Kept in its own table rather than a JSON blob on ``seen_items`` because the
    interesting queries are across comments — which authors recur, how long
    reactions run — and those are awkward against nested JSON.

    There is no score column: the RSS path exposes none (see ``RedditSource``).
    ``position`` preserves the source's own ordering, which is the only ranking
    signal available.
    """

    __tablename__ = "item_comments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seen_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seen_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    item: Mapped["SeenItem"] = relationship(back_populates="comments")
