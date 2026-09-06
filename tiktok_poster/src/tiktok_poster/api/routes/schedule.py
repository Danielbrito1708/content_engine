import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.core import settings
from src.tiktok_poster.accounts.crypto import decrypt_token
from src.tiktok_poster.accounts.repository import get_credentials
from src.tiktok_poster.buffer.client import BufferClient, BufferRateLimited, BufferRejected
from src.tiktok_poster.buffer.scheduler import (
    continuation_slot,
    next_available_slot,
    timing_bucket,
    warmup_cap_for_day,
)
from src.tiktok_poster.db.engine import get_session
from src.tiktok_poster.db.models import AccountCredentials, Publication
from src.tiktok_poster.hashtags.selector import compose_caption, select_hashtags
from src.tiktok_poster.schemas.schedule import ScheduleRequest, ScheduleResponse
from src.tiktok_poster.storage.client import generate_presigned_url
from src.tiktok_poster.youtube.metadata import build_metadata, compose_title, youtube_category_id

router = APIRouter()
log = get_logger(__name__)


async def _resolve_account(body: ScheduleRequest, session: AsyncSession) -> AccountCredentials | None:
    """Credenciais da conta pedida, ou `None` para cair na conta default.

    `account_id` ausente é o caso comum (todo orchestrador anterior à Fase 1 do
    multi-account, e a maioria dos runs hoje). `account_id` presente mas sem
    credencial cadastrada é falha de operador, não estado normal — mas também
    não pode derrubar a publicação: cai na conta default com um aviso, em vez
    de perder um vídeo já renderizado. Ver docs/multi_account.md.
    """
    if not body.account_id:
        return None
    try:
        account_uuid = uuid.UUID(body.account_id)
    except ValueError:
        log.warning("account_id inválido, usando conta default", account_id=body.account_id)
        return None
    credentials = await get_credentials(session, account_uuid)
    if credentials is None:
        log.warning("account_id sem credencial cadastrada, usando conta default", account_id=body.account_id)
    return credentials


def _warmup_started_on(account: AccountCredentials | None, platform: str) -> date | None:
    """Data de início da rampa deste canal, ou `None` (ritmo cheio, sem rampa).

    Conta extra: coluna do banco, cadastrada em `POST /accounts`. Conta
    default (`account is None`): `config.ini [warmup]` — ela nunca ganhou
    linha em `account_credentials` na Fase 1 do multi-account, e não é o caso
    que está mudando aqui. Ver docs/vision.md → "Rampa de publicação".
    """
    if account is not None:
        return account.tiktok_warmup_started_on if platform == "tiktok" else account.youtube_warmup_started_on
    warmup_cfg = getattr(settings.CONFIG, "warmup", None)
    raw = str(getattr(warmup_cfg, f"{platform}_started_on", "") or "") if warmup_cfg is not None else ""
    return date.fromisoformat(raw) if raw else None


def _warmup_steps() -> str:
    """Degraus da rampa (`"1x7,2x7,3"`) — compartilhados entre contas e
    canais, é política e não dado por conta (ver `docs/aquecimento.md`)."""
    warmup_cfg = getattr(settings.CONFIG, "warmup", None)
    return str(getattr(warmup_cfg, "steps", "") or "") if warmup_cfg is not None else ""


def _same_day_count(posts: list[dict], day: date) -> int:
    """Quantos `posts` (do formato de `BufferClient.get_pending_posts`) caem
    no mesmo dia UTC que `day` — usado para conferir o teto de aquecimento de
    um canal específico contra a fila que só ele enxerga."""
    count = 0
    for post in posts:
        try:
            post_day = datetime.fromtimestamp(int(post.get("due_at", 0)), tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            continue
        if post_day == day:
            count += 1
    return count


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


async def _record_publication(
    session: AsyncSession,
    *,
    body: ScheduleRequest,
    channel: str,
    buffer_post_id: str,
    scheduled_at: datetime,
    hashtag_variant: str,
    bucket: str,
) -> None:
    """Grava a publicação bem-sucedida — a base do teste A/B por variante.

    Chamada só depois de `create_post` ter devolvido um id de verdade: um
    `BufferRejected`/`BufferRateLimited` sai da rota antes de chegar aqui, e o
    caminho de "sem id" do YouTube (`_schedule_youtube`) também retorna antes.
    Sem linha, sem publicação — é o que torna `publications` confiável como
    fonte para `GET /analytics/variants`.
    """
    session.add(
        Publication(
            series_id=uuid.UUID(body.series_id),
            part_number=body.part_number,
            channel=channel,
            buffer_post_id=buffer_post_id,
            scheduled_at=scheduled_at,
            hashtag_variant=hashtag_variant,
            timing_bucket=bucket,
            template_id=body.template_id,
            tts_voice=body.tts_voice,
        )
    )
    await session.commit()


async def _schedule_youtube(
    body: ScheduleRequest,
    video_url: str,
    slot: datetime,
    cta: str,
    hints: list[str],
    pool: list[str],
    max_total: int,
    binary_cta: str,
    account: AccountCredentials | None = None,
    *,
    session: AsyncSession,
    timing_bucket_label: str,
    default_cap: int,
) -> tuple[str | None, str | None, bool]:
    """Agenda a mesma parte no canal do YouTube.

    Devolve `(update_id, erro, destino_ligado)`.

    **Nunca levanta.** O YouTube é destino secundário: o vídeo já está agendado
    no TikTok quando esta função roda, e derrubar o request aqui faria o
    orchestrador tratar como falha um run cujo post principal saiu — pior, o
    retry reagendaria o TikTok e a parte sairia duas vezes lá. Todo desfecho
    ruim vira string de erro, que sobe no `youtube_error` da resposta e vira
    aviso de degradação no orchestrador.

    ``account`` são as credenciais resolvidas em `_resolve_account` — `None`
    cai no canal/config default de sempre, exatamente como antes de existirem
    contas extras.

    ``default_cap`` é o teto de regime cheio (`posts_per_day`), repassado só
    para a rampa de aquecimento ter um piso quando os degraus se esgotam sem
    um regime permanente configurado.
    """
    cfg = settings.CONFIG
    yt_cfg = getattr(cfg, "youtube", None)
    channel_id = account.youtube_channel_id if account is not None else _youtube_channel_id()

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
        # sair com a mesma cauda de hashtags nos dois. Guardada numa variável
        # (em vez de só passada inline) porque `_record_publication` precisa
        # dela depois — é o `hashtag_variant` desta publicação.
        hashtag_seed = f"youtube:{body.series_id}:{body.part_number}"
        hashtags = select_hashtags(hints, mandatory, pool, max_total, seed=hashtag_seed)
        description = compose_caption(cta, hashtags, body.part_number, body.total_parts, binary_cta)
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

        if account is not None:
            youtube = BufferClient(
                channel_id=channel_id,
                access_token=decrypt_token(account.buffer_token_enc),
                org_id=account.buffer_org_id,
            )
        else:
            youtube = BufferClient(channel_id=channel_id)

        # Rampa de aquecimento — só para posts de história nova (unidade =
        # história, não post): uma continuação de série nunca é limitada por
        # ela, mesma exceção que `continuation_slot` já faz para
        # `preferred_times`/`posts_per_day` no TikTok. O YouTube tem fila
        # própria — o teto do dia é contado contra ela, não contra a do
        # TikTok, porque os dois canais podem estar em fases de rampa
        # diferentes (ver docs/vision.md → "Rampa de publicação").
        if body.follows_at is None:
            yt_started_on = _warmup_started_on(account, "youtube")
            if yt_started_on is not None:
                cap = warmup_cap_for_day(yt_started_on, _warmup_steps(), slot.date(), default_cap)
                pending_yt = await youtube.get_pending_posts()
                if _same_day_count(pending_yt, slot.date()) >= cap:
                    log.info(
                        "youtube em rampa: post fora do teto do dia",
                        series_id=body.series_id,
                        part=body.part_number,
                        cap=cap,
                        dia=slot.date().isoformat(),
                    )
                    return None, "fora do teto de aquecimento do dia", True

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

    await _record_publication(
        session,
        body=body,
        channel="youtube",
        buffer_post_id=update_id,
        scheduled_at=slot,
        hashtag_variant=hashtag_seed,
        bucket=timing_bucket_label,
    )
    return update_id, None, True


@router.post("/schedule", response_model=ScheduleResponse, status_code=201)
async def schedule(body: ScheduleRequest, session: AsyncSession = Depends(get_session)) -> ScheduleResponse:
    log.info("schedule request", series_id=body.series_id, part=body.part_number, account_id=body.account_id)

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

    account = await _resolve_account(body, session)
    if account is not None:
        buffer = BufferClient(
            channel_id=account.tiktok_channel_id,
            access_token=decrypt_token(account.buffer_token_enc),
            org_id=account.buffer_org_id,
        )
    else:
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
        # Rampa de aquecimento (unidade = história): só se aplica aqui, no
        # único ramo que decide o slot de uma história nova. Uma continuação
        # de série (ramo acima) nunca passa por ela — mesma exceção que
        # `continuation_slot` já faz para `preferred_times`.
        tiktok_started_on = _warmup_started_on(account, "tiktok")
        if tiktok_started_on is not None:
            steps = _warmup_steps()
            cap_for_day = (
                lambda day, _s=tiktok_started_on, _st=steps, _d=posts_per_day: warmup_cap_for_day(_s, _st, day, _d)
            )
        else:
            cap_for_day = posts_per_day
        slot = next_available_slot(pending, cap_for_day, preferred_times, queue_limit)
    if slot is None:
        raise _queue_full(len(pending), queue_limit)

    hashtag_data = _load_hashtag_config()
    pool: list[str] = hashtag_data.get("pool", [])
    hints: list[str] = body.classification.get("hashtag_hints", [])
    # Guardada numa variável, e não só passada inline: é o `hashtag_variant`
    # gravado em `Publication` pela `_record_publication` mais abaixo — hoje
    # calculada e descartada, agora vira o rótulo dessa variante no teste A/B.
    tiktok_hashtag_seed = f"tiktok:{body.series_id}:{body.part_number}"
    hashtags = select_hashtags(hints, mandatory, pool, max_total, seed=tiktok_hashtag_seed)

    cta_list: list[str] = body.classification.get("cta_per_part", [])
    cta = cta_list[body.part_number - 1] if body.part_number <= len(cta_list) else ""
    binary_cta: str = body.classification.get("binary_cta", "")
    caption = compose_caption(cta, hashtags, body.part_number, body.total_parts, binary_cta)

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

    # Rótulo de horário — o mesmo `slot` vale para os dois destinos (ver
    # "uma decisão de ritmo só" acima), então o rótulo também é um só.
    bucket_label = timing_bucket(slot, preferred_times)
    await _record_publication(
        session,
        body=body,
        channel="tiktok",
        buffer_post_id=update_id,
        scheduled_at=slot,
        hashtag_variant=tiktok_hashtag_seed,
        bucket=bucket_label,
    )

    youtube_id, youtube_error, youtube_enabled = await _schedule_youtube(
        body, video_url, slot, cta, hints, pool, max_total, binary_cta, account=account,
        session=session, timing_bucket_label=bucket_label, default_cap=posts_per_day,
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
