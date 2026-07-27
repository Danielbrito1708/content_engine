import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
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
    #: Comments the source reported, ``None`` when they were never fetched.
    #: Enrichment costs a rate-limit window per item, so only published
    #: candidates carry it — ``None`` means "not looked at", not "zero replies".
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[SeenStatus] = mapped_column(Enum(SeenStatus), nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    comments: Mapped[list["ItemComment"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", lazy="selectin"
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
