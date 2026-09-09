import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobStatus(str, PyEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    video_file_key: Mapped[str] = mapped_column(String(512), nullable=False)
    music_key: Mapped[str] = mapped_column(String(512), nullable=False)
    voice_key: Mapped[str] = mapped_column(String(512), nullable=False)
    subtitle_key: Mapped[str] = mapped_column(String(512), nullable=False)
    #: PNG do card de comentário, mostrado durante a intro. Nullable: quem monta
    #: o vídeo pode não ter card, e isso não impede o render.
    card_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Áudio da frase gancho, narrado sobre o card antes da narração da parte.
    hook_voice_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Quando `True`, o áudio do gancho entra só como duração do card e não é
    #: tocado — é o caso da parte cuja própria narração já abre com a frase.
    hook_muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    video_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    blend_key: Mapped[str] = mapped_column(String(512), nullable=False)
    #: `template.json` — narrowed since the VSEL cutover (08/09/2026) to the
    #: one thing that never moved into the YAML: `narration.rate`, read by
    #: the orchestrator via `GET /templates/{id}/config` before any render
    #: happens. Everything else it used to carry (channels, card, subtitles,
    #: cta, timing) is now expressed in `yaml_key`.
    json_key: Mapped[str] = mapped_column(String(512), nullable=False)
    #: The VSEL timeline (`docs/edicao_declarativa.md`), an object in the
    #: same bucket as `blend_key`/`json_key`. Nullable because the column
    #: predates every existing row — a template with no `yaml_key` cannot be
    #: rendered (`worker.py` raises rather than falling back to the retired
    #: `template.json`-driven `main()`; see `blender_worker/CLAUDE.md` §
    #: "VSEL — Declarative timeline resolver").
    yaml_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id"), nullable=False
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("templates.id"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), nullable=False, default=JobStatus.pending
    )
    output_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    blend_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
