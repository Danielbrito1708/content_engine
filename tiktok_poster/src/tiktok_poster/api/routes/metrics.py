"""Sincronização de métricas do Buffer e a análise por variante (teste A/B).

As duas rotas vivem no mesmo arquivo por compartilharem as mesmas duas tabelas
(`Publication`/`PostMetric`) e nenhuma ter lógica suficiente para justificar um
módulo próprio — ver `tiktok_poster/CLAUDE.md` para o design completo.
"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.tiktok_poster.buffer.client import BufferClient
from src.tiktok_poster.db.engine import get_session
from src.tiktok_poster.db.models import PostMetric, Publication

router = APIRouter()
log = get_logger(__name__)

#: ~24h de atraso documentado pelo Buffer para computar métricas de um post
#: (https://developers.buffer.com/guides/post-metrics.html). Sondar antes
#: disso é chamada desperdiçada: o Buffer ainda não tem nada a dizer.
METRICS_LAG = timedelta(hours=24)

#: Não re-sondar uma publicação que já tem métrica coletada há menos que isto.
#: O sync roda 1x/dia via cron externo (ver CLAUDE.md); dentro da mesma janela
#: não há nada novo para buscar, só cota gasta à toa (250 chamadas/dia — ver
#: "A cota da API do Buffer" no CLAUDE.md deste serviço).
RESYNC_COOLDOWN = timedelta(hours=20)

#: Default do `limit` de `/metrics/sync` — baixo de propósito. A varredura de
#: retry do orchestrador já come boa parte da cota diária do Buffer (ver
#: CLAUDE.md); este endpoint não pode competir com `/schedule` por chamadas.
DEFAULT_SYNC_LIMIT = 50


class SyncResponse(BaseModel):
    #: Publicações com pelo menos uma métrica gravada nesta chamada.
    synced: int
    #: Total de linhas `PostMetric` inseridas (uma publicação pode gerar várias
    #: — uma por tipo de métrica que o Buffer devolveu).
    metrics_written: int
    #: Publicações cuja chamada ao Buffer falhou (rede/HTTP) — não abortam o
    #: lote, só contam aqui.
    failed: int
    #: Publicações velhas o bastante mas já sincronizadas dentro do cooldown de
    #: `RESYNC_COOLDOWN` — informativo, não é erro nem falta de dado.
    skipped: int


def _parse_metrics_updated_at(raw) -> datetime | None:
    """Best-effort: o Buffer não garante formato, e isto nunca deve abortar o sync."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


@router.post("/metrics/sync", response_model=SyncResponse)
async def sync_metrics(
    limit: int = DEFAULT_SYNC_LIMIT,
    session: AsyncSession = Depends(get_session),
) -> SyncResponse:
    """Busca métricas no Buffer para publicações elegíveis e grava snapshots.

    Elegível = agendada há mais de `METRICS_LAG` (o atraso documentado do
    Buffer) **e** sem `PostMetric` gravado nas últimas `RESYNC_COOLDOWN` horas.
    Pensado para ser chamado 1x/dia por cron externo — não há loop novo dentro
    do processo (mesma decisão do cron de disco pendente em `docs/deploy.md`).

    Uma falha numa publicação (rede, HTTP) é contada em `failed` e não impede
    as demais do lote — o objetivo é coletar o que der, não tudo ou nada.
    """
    now = datetime.now(tz=timezone.utc)
    cutoff = now - METRICS_LAG
    cooldown_cutoff = now - RESYNC_COOLDOWN

    # Publicações com métrica gravada dentro do cooldown — excluídas da
    # varredura porque não há nada novo para buscar ainda.
    recently_synced = (
        select(PostMetric.publication_id)
        .where(PostMetric.fetched_at >= cooldown_cutoff)
        .distinct()
    )

    eligible_stmt = select(Publication).where(Publication.scheduled_at <= cutoff)
    stmt = (
        eligible_stmt.where(Publication.id.not_in(recently_synced))
        .order_by(Publication.scheduled_at.asc())
        .limit(limit)
    )
    publications = (await session.execute(stmt)).scalars().all()

    skipped_stmt = select(func.count()).select_from(
        eligible_stmt.where(Publication.id.in_(recently_synced)).subquery()
    )
    skipped = (await session.execute(skipped_stmt)).scalar_one()

    buffer = BufferClient()
    synced = 0
    metrics_written = 0
    failed = 0

    for pub in publications:
        try:
            result = await buffer.get_post_metrics(pub.buffer_post_id)
        except Exception as exc:  # noqa: BLE001 — um post ruim não pode abortar o lote
            log.warning(
                "metrics sync failed for publication",
                publication_id=str(pub.id),
                buffer_post_id=pub.buffer_post_id,
                error=str(exc),
            )
            failed += 1
            continue

        if result is None:
            # Post recente demais para o Buffer ter computado métricas — normal,
            # documentado, não é falha. A próxima passada tenta de novo.
            continue

        metrics_updated_at = _parse_metrics_updated_at(result.get("metrics_updated_at"))
        wrote_any = False
        for metric in result.get("metrics", []):
            session.add(
                PostMetric(
                    publication_id=pub.id,
                    metric_type=metric["type"],
                    value=float(metric["value"]),
                    unit=metric.get("unit") or "",
                    metrics_updated_at=metrics_updated_at,
                )
            )
            metrics_written += 1
            wrote_any = True
        if wrote_any:
            synced += 1

    await session.commit()

    return SyncResponse(synced=synced, metrics_written=metrics_written, failed=failed, skipped=skipped)


class VariantStat(BaseModel):
    #: Valor da dimensão agrupada (ex. a seed de hashtag, "11h", o UUID do
    #: template) — "none" quando a coluna era nula (`template_id`/`tts_voice`
    #: em publicações de antes desses campos existirem).
    variant: str
    sample_size: int
    avg: float
    min: float
    max: float
    sum: float


_DIMENSION_COLUMNS = {
    "hashtag_variant": Publication.hashtag_variant,
    "timing_bucket": Publication.timing_bucket,
    "template_id": Publication.template_id,
    "tts_voice": Publication.tts_voice,
}


@router.get("/analytics/variants", response_model=list[VariantStat])
async def analytics_variants(
    dimension: Literal["hashtag_variant", "timing_bucket", "template_id", "tts_voice"] = Query(...),
    metric_type: str = Query("views", min_length=1),
    channel: Literal["tiktok", "youtube"] | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[VariantStat]:
    """Compara o desempenho médio das publicações agrupadas por variante.

    Descritivo, sem teste de significância estatística — decisão já tomada
    (ver `docs/vision.md`). Só entram publicações com **pelo menos uma**
    `PostMetric` do `metric_type` pedido: ausência de métrica não é a mesma
    coisa que métrica zero, e contar como zero enviesaria a média para baixo
    exatamente nas variantes menos sincronizadas, não nas piores.
    """
    dimension_col = _DIMENSION_COLUMNS[dimension]

    # Snapshot mais recente de `metric_type` por publicação — uma publicação
    # pode ter várias linhas ao longo do tempo (ver `PostMetric`), e só a mais
    # nova de cada uma entra na agregação.
    ranked = (
        select(
            PostMetric.publication_id,
            PostMetric.value,
            func.row_number()
            .over(partition_by=PostMetric.publication_id, order_by=PostMetric.fetched_at.desc())
            .label("rn"),
        )
        .where(PostMetric.metric_type == metric_type)
        .subquery()
    )
    latest = select(ranked.c.publication_id, ranked.c.value).where(ranked.c.rn == 1).subquery()

    stmt = (
        select(
            dimension_col.label("variant"),
            func.count().label("sample_size"),
            func.avg(latest.c.value).label("avg"),
            func.min(latest.c.value).label("min"),
            func.max(latest.c.value).label("max"),
            func.sum(latest.c.value).label("sum"),
        )
        .select_from(Publication)
        .join(latest, latest.c.publication_id == Publication.id)
        .group_by(dimension_col)
    )
    if channel is not None:
        stmt = stmt.where(Publication.channel == channel)
    stmt = stmt.order_by(func.count().desc())

    rows = (await session.execute(stmt)).all()
    return [
        VariantStat(
            variant="none" if row.variant is None else str(row.variant),
            sample_size=row.sample_size,
            avg=float(row.avg),
            min=float(row.min),
            max=float(row.max),
            sum=float(row.sum),
        )
        for row in rows
    ]
