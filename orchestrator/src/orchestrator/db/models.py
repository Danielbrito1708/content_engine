import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PipelineStatus(str, PyEnum):
    pending = "pending"
    refining = "refining"
    refined = "refined"
    processing = "processing"
    scheduling = "scheduling"
    scheduled = "scheduled"
    posted = "posted"
    failed = "failed"


class PartStatus(str, PyEnum):
    pending = "pending"
    tts_running = "tts_running"
    tts_done = "tts_done"
    render_pending = "render_pending"
    render_running = "render_running"
    render_done = "render_done"
    failed = "failed"


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_script: Mapped[str] = mapped_column(Text, nullable=False)
    input_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    refined_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parts_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[PipelineStatus] = mapped_column(Enum(PipelineStatus), nullable=False, default=PipelineStatus.pending)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    parts: Mapped[list["PipelinePart"]] = relationship("PipelinePart", back_populates="run", order_by="PipelinePart.part_number")


class PipelinePart(Base):
    __tablename__ = "pipeline_parts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("pipeline_runs.id"), nullable=False)
    part_number: Mapped[int] = mapped_column(Integer, nullable=False)
    script: Mapped[str] = mapped_column(Text, nullable=False)
    audio_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    video_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    blender_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[PartStatus] = mapped_column(Enum(PartStatus), nullable=False, default=PartStatus.pending)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tiktok_video_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    run: Mapped["PipelineRun"] = relationship("PipelineRun", back_populates="parts")
