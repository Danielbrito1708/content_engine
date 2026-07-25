import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
    status: Mapped[SeenStatus] = mapped_column(Enum(SeenStatus), nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
