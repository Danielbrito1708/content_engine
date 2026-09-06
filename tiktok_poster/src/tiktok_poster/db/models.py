import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AccountCredentials(Base):
    """Credenciais do Buffer de uma conta, isoladas por conta e cifradas.

    `account_id` é o mesmo id da linha `accounts` do `orchestrator` — mas os
    dois bancos são serviços diferentes, sem FK entre eles. O orchestrador
    nunca vê `buffer_token_enc`: ele só manda `account_id` no `POST /schedule`
    e este serviço resolve a credencial sozinho. Ver docs/multi_account.md,
    seção "Onde as credenciais moram".
    """

    __tablename__ = "account_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    #: Só para inspeção humana (`GET /accounts`) — nunca usado para autenticar.
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Token de acesso do Buffer, cifrado com Fernet (ver `accounts/crypto.py`).
    #: Nunca sai em texto claro de nenhum endpoint.
    buffer_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    buffer_org_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tiktok_channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    youtube_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Data de início da rampa de aquecimento para cada canal desta conta.
    #: `None` = canal com histórico, ritmo cheio — não é o estado inicial
    #: automático de uma conta nova, é uma escolha explícita de quem cadastra
    #: (ver docs/vision.md → "Rampa de publicação"). Os dois são independentes
    #: porque o TikTok e o YouTube da mesma conta podem estar em fases
    #: diferentes de aquecimento.
    tiktok_warmup_started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    youtube_warmup_started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Publication(Base):
    """Uma parte publicada com sucesso num canal — TikTok ou YouTube.

    Gravada só no caminho de sucesso de `POST /schedule` (ver
    `api/routes/schedule.py::_record_publication`): se o Buffer recusa o post
    (`BufferRejected`) ou estoura cota (`BufferRateLimited`), nenhuma linha
    entra aqui. É o que torna esta tabela a base confiável do teste A/B — uma
    publicação registrada é uma publicação que de fato saiu.

    `hashtag_variant` e `timing_bucket` são as duas variáveis testadas que só
    este serviço conhece (a seed de `select_hashtags` e o slot escolhido pelo
    scheduler); `template_id`/`tts_voice` chegam prontos do orchestrador, que é
    quem decide o template de edição e recebe a voz do `tts_service`.
    """

    __tablename__ = "publications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: UUID do pipeline run no orchestrador. Sem FK entre os bancos, como
    #: `AccountCredentials.account_id` — serviços diferentes, sem tabela em comum.
    series_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    part_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `"tiktok"` ou `"youtube"` — os dois destinos de `_schedule_youtube`.
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    #: ID do post no Buffer. Único porque cada `create_post` bem-sucedido cria
    #: exatamente um post — reagendar a mesma parte não deveria duplicar a linha,
    #: mas hoje não há upsert: um retry publicaria de novo no Buffer também.
    buffer_post_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: A seed usada em `select_hashtags(..., seed=...)` — ex. `tiktok:{series_id}:{part}`.
    #: Guardada como string porque é a seed, não o resultado: duas publicações
    #: com a mesma seed tiveram a mesma cauda de hashtags, mesmo que o pool
    #: tenha mudado desde então.
    hashtag_variant: Mapped[str] = mapped_column(String(255), nullable=False)
    #: `"11h"`/`"15h"`/`"19h"` para os horários da grade, `"other"` para
    #: continuação de série (que pendura fora de `preferred_times`). Ver
    #: `buffer/scheduler.py::timing_bucket`.
    timing_bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Nullable: um orchestrador anterior a este campo, ou um disparo manual sem
    #: `template_id`, não sabe qual template rodou.
    template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: Nullable pela mesma razão — depende do `tts_service` devolver `voice` na
    #: resposta de `/generate`, que é mudança de outro serviço.
    tts_voice: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PostMetric(Base):
    """Um snapshot de uma métrica do Buffer para uma publicação, num instante.

    Snapshot, não valor único: `POST /metrics/sync` roda repetidamente (cron
    externo, 1x/dia) e cada chamada grava uma linha nova por tipo de métrica,
    em vez de sobrescrever a anterior. Isso permite ver evolução ao longo do
    tempo; `GET /analytics/variants` usa só o snapshot mais recente por
    `(publication_id, metric_type)` — métrica ausente não é a mesma coisa que
    métrica zero, ver a rota.
    """

    __tablename__ = "post_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publications.id"), nullable=False, index=True
    )
    #: `views`, `likes`, `shares`, `comments`, `reach`, `impressions`, `saves`,
    #: `totalTimeWatched`, `reactions`, `reposts`, `follows`, `quotes`,
    #: `viewers` — os tipos documentados pelo Buffer, guardados como o Buffer
    #: os nomeia, sem tradução.
    metric_type: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Quando o Buffer calculou este valor — pode ser bem anterior a `fetched_at`
    #: (a coleta é diária, a métrica em si é computada por eles com ~24h de
    #: atraso). Nullable porque a resposta do Buffer pode não trazer o campo.
    metrics_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
