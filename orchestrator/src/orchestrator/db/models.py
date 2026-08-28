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
    #: Frase gancho devolvida pelo refino — a primeira frase da parte 1.
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Áudio da frase gancho, narrado sozinho. `None` quando o refino não
    #: devolveu gancho ou quando o TTS do gancho falhou (ver `_run_hook_tts`).
    hook_audio_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    hook_srt_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: PNG do card de comentário com a frase gancho, mostrado na intro de todas
    #: as partes. `None` quando não há gancho ou quando a composição falhou.
    card_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Gênero de quem narra a história (`male` / `female` / `unknown`), vindo do
    #: refino. Escolhe a voz da narração no `tts_service`. `None` só num run
    #: criado antes deste campo existir.
    narrator_gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Título do vídeo no YouTube, vindo do refino. É do run e não da parte:
    #: uma história dividida é a mesma história, e o que distingue as partes é o
    #: rótulo "(Parte n/N)", que o poster acrescenta por saber `total_parts`.
    youtube_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
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
    srt_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    video_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    blender_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[PartStatus] = mapped_column(Enum(PartStatus), nullable=False, default=PartStatus.pending)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tiktok_video_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: ID do post no YouTube. `None` quando o canal não está conectado ou quando
    #: o agendamento lá falhou — o run segue `scheduled` nos dois casos, porque
    #: o YouTube é destino secundário. É esta coluna que torna a ausência
    #: auditável depois do aviso ter passado.
    youtube_video_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Clipe de fundo sobre o qual esta parte foi renderizada. É gravado antes do
    #: render, não depois: é ele que faz um re-render reusar a mesma footage, e é
    #: a contagem destas linhas que diz à rotação quais clipes ainda não saíram.
    #: `None` nas partes anteriores à rotação — elas não contam para o ciclo.
    background_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    run: Mapped["PipelineRun"] = relationship("PipelineRun", back_populates="parts")
