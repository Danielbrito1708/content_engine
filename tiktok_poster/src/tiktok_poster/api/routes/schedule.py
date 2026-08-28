import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from structlog import get_logger

from src.core import settings
from src.tiktok_poster.buffer.client import BufferClient, BufferRateLimited, BufferRejected
from src.tiktok_poster.buffer.scheduler import continuation_slot, next_available_slot
from src.tiktok_poster.hashtags.selector import compose_caption, select_hashtags
from src.tiktok_poster.schemas.schedule import ScheduleRequest, ScheduleResponse
from src.tiktok_poster.storage.client import generate_presigned_url
from src.tiktok_poster.youtube.metadata import build_metadata, compose_title, youtube_category_id

router = APIRouter()
log = get_logger(__name__)


def _load_hashtag_config() -> dict:
    path = Path(settings.ROOT_DIR) / "hashtags.json"
    with open(path) as f:
        return json.load(f)


def _split_csv(raw: str) -> list[str]:
    return [t.strip() for t in str(raw).split(",") if t.strip()]


#: Marcadores de teto na mensagem de recusa do Buffer. A API não devolve código
#: de erro para isso — só texto —, então a lista é heurística e proposital:
#: errar para o lado de "fila cheia" custa uma espera, errar para o outro lado
#: custa o run inteiro.
_QUEUE_LIMIT_MARKERS = ("limit", "queue is full", "too many", "maximum", "plan")


def _looks_like_queue_limit(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _QUEUE_LIMIT_MARKERS)


def _rate_limited(exc: BufferRateLimited) -> HTTPException:
    """Cota da API estourada — mesmo `429` da fila cheia, motivo diferente.

    O status é o mesmo de propósito: o orchestrador já lê `429` como "não é
    falha, o run espera e é reoferecido". O que muda é o `error`, para o aviso
    dizer a verdade — fila cheia se resolve publicando, cota se resolve
    esperando a janela virar, e confundir as duas manda procurar no lugar errado.
    """
    return HTTPException(
        status_code=429,
        detail={
            "error": "buffer_rate_limited",
            "message": str(exc),
            "retry_after": exc.retry_after,
        },
    )


def _queue_full(pending_count: int | None, queue_limit: int, reason: str | None = None) -> HTTPException:
    """O `429` que o orchestrador lê como backpressure, num lugar só."""
    return HTTPException(
        status_code=429,
        detail={
            "error": "buffer_queue_full",
            "message": f"Buffer queue has reached the {queue_limit}-post limit. Retry later.",
            "pending_count": pending_count,
            # Presente só quando o teto veio da recusa do Buffer, não da
            # contagem local: é a diferença entre "sabíamos antes de tentar" e
            # "descobrimos ao tentar", e é o que se lê no log do orchestrador.
            "rejected_by_buffer": reason,
        },
    )


async def _pending_count(buffer: BufferClient) -> int | None:
    """Quantos posts pendentes o Buffer diz ter **agora**, ou `None` se não deu.

    Roda no caminho de erro, para decidir se a recusa foi teto de fila. Não pode
    levantar: a exceção original é que interessa, e uma segunda falha aqui não
    pode substituí-la.
    """
    try:
        return len(await buffer.get_pending_posts())
    except Exception:  # noqa: BLE001 — segunda chance, não segunda falha
        return None


def _youtube_channel_id() -> str:
    """Canal do YouTube, ou `""` quando não há um configurado.

    Função e não leitura direta porque `settings.env` é um modelo congelado:
    esta é a costura por onde os testes ligam e desligam o destino.
    """
    return settings.env.buffer_youtube_channel_id


async def _schedule_youtube(
    body: ScheduleRequest,
    video_url: str,
    slot: datetime,
    cta: str,
    hints: list[str],
    pool: list[str],
    max_total: int,
) -> tuple[str | None, str | None, bool]:
    """Agenda a mesma parte no canal do YouTube.

    Devolve `(update_id, erro, destino_ligado)`.

    **Nunca levanta.** O YouTube é destino secundário: o vídeo já está agendado
    no TikTok quando esta função roda, e derrubar o request aqui faria o
    orchestrador tratar como falha um run cujo post principal saiu — pior, o
    retry reagendaria o TikTok e a parte sairia duas vezes lá. Todo desfecho
    ruim vira string de erro, que sobe no `youtube_error` da resposta e vira
    aviso de degradação no orchestrador.
    """
    cfg = settings.CONFIG
    yt_cfg = getattr(cfg, "youtube", None)
    channel_id = _youtube_channel_id()

    if yt_cfg is not None and not getattr(yt_cfg, "enabled", True):
        return None, "youtube desligado no config", False
    if not channel_id:
        return None, "canal do youtube não configurado", False

    # Sem título não há post: o Buffer exige o campo. O CTA é o fallback para o
    # caso de um orchestrador antigo, que ainda não manda `youtube_title`.
    raw_title = (body.youtube_title or "").strip() or cta.strip()
    if not raw_title:
        return None, "sem título para o youtube", True

    try:
        mandatory = _split_csv(getattr(cfg.hashtags, "youtube_mandatory", ""))
        # Seed com prefixo próprio: o mesmo vídeo nos dois destinos não pode
        # sair com a mesma cauda de hashtags nos dois.
        hashtags = select_hashtags(
            hints, mandatory, pool, max_total, seed=f"youtube:{body.series_id}:{body.part_number}"
        )
        description = compose_caption(cta, hashtags, body.part_number, body.total_parts)
        title = compose_title(raw_title, body.part_number, body.total_parts)

        metadata = build_metadata(
            title=title,
            category_id=youtube_category_id(
                body.classification.get("content_type"),
                default=str(getattr(yt_cfg, "category_id", "22")),
            ),
            privacy=str(getattr(yt_cfg, "privacy", "public")),
            made_for_kids=bool(getattr(yt_cfg, "made_for_kids", False)),
            notify_subscribers=bool(getattr(yt_cfg, "notify_subscribers", True)),
            ai_disclosed=bool(getattr(yt_cfg, "ai_disclosed", True)),
        )

        youtube = BufferClient(channel_id=channel_id)
        data = await youtube.create_post(video_url, description, slot, metadata=metadata)
        update_id = str(data.get("updates", [{}])[0].get("id", ""))
    except Exception as exc:  # noqa: BLE001 — destino secundário não derruba o principal
        log.warning(
            "youtube schedule failed",
            series_id=body.series_id,
            part=body.part_number,
            error=str(exc),
        )
        return None, str(exc), True

    if not update_id:
        return None, "buffer não devolveu id do post no youtube", True
    return update_id, None, True


@router.post("/schedule", response_model=ScheduleResponse, status_code=201)
async def schedule(body: ScheduleRequest) -> ScheduleResponse:
    log.info("schedule request", series_id=body.series_id, part=body.part_number)

    cfg = settings.CONFIG
    posting_cfg = cfg.posting
    posts_per_day: int = posting_cfg.posts_per_day
    preferred_times: list[str] = _split_csv(posting_cfg.preferred_times)
    queue_limit: int = posting_cfg.buffer_queue_limit
    ttl: int = posting_cfg.presigned_url_ttl
    gap_minutes: int = posting_cfg.series_gap_minutes

    hashtag_cfg = cfg.hashtags
    mandatory: list[str] = _split_csv(hashtag_cfg.mandatory)
    max_total: int = hashtag_cfg.max_total

    buffer = BufferClient()

    # O slot sai da fila do TikTok e vale para os dois destinos: é o TikTok que
    # dita o ritmo de publicação, e a mesma história em dois lugares no mesmo
    # horário é uma decisão de ritmo só.
    try:
        pending = await buffer.get_pending_posts()
    except BufferRateLimited as exc:
        log.warning("buffer rate limited", series_id=body.series_id, part=body.part_number,
                    retry_after=exc.retry_after)
        raise _rate_limited(exc) from exc
    if body.follows_at is not None:
        slot = continuation_slot(body.follows_at, gap_minutes, pending, queue_limit)
    else:
        slot = next_available_slot(pending, posts_per_day, preferred_times, queue_limit)
    if slot is None:
        raise _queue_full(len(pending), queue_limit)

    hashtag_data = _load_hashtag_config()
    pool: list[str] = hashtag_data.get("pool", [])
    hints: list[str] = body.classification.get("hashtag_hints", [])
    hashtags = select_hashtags(
        hints, mandatory, pool, max_total, seed=f"tiktok:{body.series_id}:{body.part_number}"
    )

    cta_list: list[str] = body.classification.get("cta_per_part", [])
    cta = cta_list[body.part_number - 1] if body.part_number <= len(cta_list) else ""
    caption = compose_caption(cta, hashtags, body.part_number, body.total_parts)

    bucket = cfg.storage.bucket
    video_url = await generate_presigned_url(bucket, body.video_key, ttl)

    # A pré-checagem acima já barrou a fila cheia que **dava para saber**. Esta
    # cláusula é para o teto que só aparece na recusa: a contagem local filtra
    # `status: [scheduled]` de um canal, e o teto do Buffer não é obrigado a
    # contar do mesmo jeito. Sem isto, esse caso vira 500, o orchestrador marca
    # o run `failed` e a varredura de retry — que só olha `scheduling` — nunca
    # mais o encosta: um vídeo pronto, perdido porque a fila estava cheia num
    # minuto. Aconteceu com dois runs em 15/08/2026.
    try:
        data = await buffer.create_post(video_url, caption, slot)
    except BufferRateLimited as exc:
        log.warning("buffer rate limited on create", series_id=body.series_id,
                    part=body.part_number, retry_after=exc.retry_after)
        raise _rate_limited(exc) from exc
    except BufferRejected as exc:
        pending_now = await _pending_count(buffer)
        at_limit = pending_now is not None and pending_now >= queue_limit
        log.warning(
            "buffer rejected create_post",
            series_id=body.series_id,
            part=body.part_number,
            reason=exc.message,
            pending_now=pending_now,
            queue_limit=queue_limit,
        )
        if at_limit or _looks_like_queue_limit(exc.message):
            raise _queue_full(pending_now, queue_limit, reason=exc.message) from exc
        raise

    update_id: str = str(data.get("updates", [{}])[0].get("id", ""))

    youtube_id, youtube_error, youtube_enabled = await _schedule_youtube(
        body, video_url, slot, cta, hints, pool, max_total
    )

    log.info(
        "post scheduled",
        series_id=body.series_id,
        part=body.part_number,
        of=body.total_parts,
        slot=slot.isoformat(),
        continuation=body.follows_at is not None,
        youtube=youtube_id or youtube_error,
    )

    return ScheduleResponse(
        scheduled_at=slot,
        buffer_update_id=update_id,
        youtube_update_id=youtube_id,
        youtube_error=youtube_error,
        youtube_enabled=youtube_enabled,
    )
