"""Caixa de entrada manual: um link compartilhado no celular vira um run.

A terceira porta de entrada do sistema, ao lado do ``POST /pipeline`` manual e do
ciclo automático do scout (``docs/vision.md`` → "Trigger"). Existe porque o sinal
que ela carrega não existe em nenhuma das outras duas: o Reddit diz quantas
pessoas votaram, e a visualização de um vídeo diz que a história **prendeu**.
Quem escolhe é uma pessoa olhando o número, e o trabalho daqui é só não perder o
link entre o celular e a fila.

**Por que ntfy e não um endpoint.** O ntfy já está no ar como destino das
notificações que saem, o app já está no celular, e ele aparece na aba de
compartilhar do Android — então o caminho é TikTok → Compartilhar → ntfy, sem
digitar. Um endpoint HTTP exigiria estar na LAN de casa; um bot de Telegram
exigiria token, dependência e um serviço a mais para manter.

O tópico de entrada é **outro** tópico, não o das notificações. Reusar o mesmo
faria o serviço ler as próprias mensagens de saída e tentar tratá-las como link.

**Uma regra de produto governa este módulo**: o texto transcrito entra como
matéria-prima do refino, nunca como roteiro final — recitar palavra por palavra
expõe a conta a corte de alcance por conteúdo não-original e a strike de direito
autoral. Aqui isso é automático, porque tudo que passa pelo ``POST /pipeline``
passa pelo refino; o módulo não tem como pular essa etapa nem se quisesse.
"""

import asyncio
import json
import re

import httpx
import structlog

from src.content_scout.clients.orchestrator import OrchestratorClient
from src.content_scout.clients.tts import TranscriptionError, TranscribeClient
from src.content_scout.db.engine import AsyncSessionLocal
from src.content_scout.db.models import SeenItem, SeenStatus
from src.content_scout.filters import content_fingerprint
from src.content_scout.scout import _record, _seen_row
from src.content_scout.sources.base import Candidate
from src.core import settings
from src.core.notify import notify, short_id

log = structlog.get_logger(__name__)

SOURCE_NAME = "inbox"

#: URLs dentro da mensagem. O app do ntfy manda o texto do compartilhamento
#: inteiro, que no TikTok vem com legenda e hashtags em volta do link — daí
#: extrair em vez de exigir que a mensagem seja só a URL.
_URL_RE = re.compile(r"https?://[^\s<>\"]+")


def extract_urls(message: str) -> list[str]:
    """Todas as URLs de uma mensagem, na ordem, sem repetir.

    Uma mensagem pode legitimamente trazer mais de um link — mandar a parte 1 e a
    parte 2 de uma história de uma vez é o caso óbvio —, então isto devolve lista
    e não a primeira ocorrência.
    """
    seen: set[str] = set()
    urls = []
    for match in _URL_RE.findall(message or ""):
        url = match.rstrip(".,;)]}")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


async def _already_seen_url(session, url: str) -> bool:
    """Reenvio do mesmo link, detectado antes de gastar CPU.

    O dedup de verdade é por ``external_id`` e por fingerprint do texto, e os
    dois só ficam disponíveis **depois** de transcrever. Como reenviar o mesmo
    link é justamente o engano mais provável de quem compartilha do celular,
    vale a consulta barata antes: ela evita ~70s de CPU por engano repetido.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(SeenItem.id).where(SeenItem.url == url).limit(1)
    )
    return result.first() is not None


async def _already_seen_video(session, external_id: str, fingerprint: str | None) -> str | None:
    """Devolve o motivo do descarte, ou ``None`` se a história é nova.

    Duas perguntas diferentes: ``external_id`` pega o mesmo vídeo chegando por
    outra URL (link curto vs. canônico), e o fingerprint pega a mesma história
    republicada como outro vídeo — inclusive uma que o scout já tinha achado no
    Reddit antes, que é o cruzamento que torna esta checagem valiosa.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(SeenItem.id).where(SeenItem.external_id == external_id).limit(1)
    )
    if result.first() is not None:
        return "duplicate_video"

    if fingerprint:
        result = await session.execute(
            select(SeenItem.id).where(SeenItem.content_fingerprint == fingerprint).limit(1)
        )
        if result.first() is not None:
            return "duplicate_story"
    return None


def _build_candidate(url: str, transcription) -> Candidate:
    source = transcription.source
    title = transcription.title or f"Vídeo {transcription.video_id}"
    return Candidate(
        source=SOURCE_NAME,
        external_id=f"{SOURCE_NAME}:{transcription.video_id}",
        # A origin é o criador, no mesmo formato que `r/{sub}` tem para o Reddit:
        # é ela que responde "de onde isso veio" num `GET /scout/seen`, e saber
        # que várias histórias vieram do mesmo canal é exatamente o que se quer
        # ver antes de virar dependência de uma fonte só.
        origin=f"@{source.get('uploader') or 'desconhecido'}",
        title=title,
        text=transcription.text,
        url=source.get("video_url") or url,
        extra={
            "author": source.get("uploader"),
            "ingest": "ntfy",
            **{k: v for k, v in source.items() if k not in {"title", "uploader"}},
        },
    )


async def handle_url(url: str) -> str:
    """Processa um link. Devolve o desfecho, para o log e a notificação.

    A ordem das checagens é a mesma do ciclo do scout e pelo mesmo motivo: o que
    é barato e descarta vem antes do que é caro. Capacidade primeiro (uma
    consulta), reenvio do mesmo link depois (uma consulta), transcrição por
    último (~70s de CPU).
    """
    scout_cfg = settings.CONFIG.scout
    filters_cfg = settings.CONFIG.filters
    orchestrator = OrchestratorClient()

    active = await orchestrator.count_active_runs()
    if active >= scout_cfg.max_pending_runs:
        # Sem fila de espera: o link é recusado e quem mandou é avisado para
        # reenviar. Guardá-lo exigiria uma tabela de pendências que ainda não
        # existe, e engolir a capacidade converteria o freio de memória do
        # blender_worker em sugestão.
        log.info("inbox_no_capacity", url=url, active=active)
        notify(
            "Link recusado — fila cheia",
            level="warning",
            icon="🚧",
            link=url[:120],
            em_andamento=active,
            teto=scout_cfg.max_pending_runs,
            acao="reenvie mais tarde",
        )
        return "no_capacity"

    async with AsyncSessionLocal() as session:
        if await _already_seen_url(session, url):
            log.info("inbox_duplicate_url", url=url)
            notify("Link já recebido antes", level="debug", icon="🔁", link=url[:120])
            return "duplicate_url"

    notify("Baixando e transcrevendo", level="debug", icon="⬇️", link=url[:120])

    try:
        transcription = await TranscribeClient().transcribe(url)
    except TranscriptionError as exc:
        log.warning("inbox_transcribe_failed", url=url, error=str(exc), retryable=exc.retryable)
        notify(
            "Não consegui transcrever",
            level="warning",
            icon="⚠️",
            link=url[:120],
            erro=str(exc)[:160],
            acao="tente de novo mais tarde" if exc.retryable else None,
        )
        return "transcribe_failed"

    candidate = _build_candidate(url, transcription)
    fingerprint = content_fingerprint(candidate.text)

    async with AsyncSessionLocal() as session:
        duplicate = await _already_seen_video(session, candidate.external_id, fingerprint)
        if duplicate:
            log.info("inbox_duplicate", url=url, reason=duplicate)
            await _record(session, _seen_row(candidate, SeenStatus.filtered, skip_reason=duplicate))
            notify(
                "História repetida",
                level="info",
                icon="🔁",
                motivo=duplicate,
                titulo=candidate.title[:80],
            )
            return duplicate

        # Os mesmos limites do ciclo automático, e não um conjunto próprio: um
        # roteiro curto demais não vira vídeo por ter vindo pelo celular. O piso
        # também é a rede contra transcrição truncada, que é o modo de falha real
        # aqui — vídeo em partes corta a história no meio.
        if candidate.char_count < filters_cfg.min_chars:
            reason = f"too_short:{candidate.char_count}"
            await _record(session, _seen_row(candidate, SeenStatus.filtered, skip_reason=reason))
            log.info("inbox_filtered", url=url, reason=reason)
            notify(
                "Transcrição curta demais",
                level="warning",
                icon="✂️",
                chars=candidate.char_count,
                minimo=filters_cfg.min_chars,
                titulo=candidate.title[:80],
            )
            return reason

        metadata = candidate.to_metadata()
        try:
            run_id = await orchestrator.create_pipeline(script=candidate.text, metadata=metadata)
        except Exception as exc:  # noqa: BLE001
            log.warning("inbox_submit_failed", url=url, error=str(exc))
            await _record(
                session,
                _seen_row(candidate, SeenStatus.failed, skip_reason=str(exc)[:255]),
            )
            notify("Falha ao enfileirar", level="error", icon="❌", erro=str(exc)[:160])
            return "submit_failed"

        await _record(
            session,
            _seen_row(candidate, SeenStatus.submitted, pipeline_run_id=run_id),
        )

    log.info(
        "inbox_submitted",
        url=url,
        run_id=str(run_id),
        chars=candidate.char_count,
        views=transcription.source.get("view_count"),
    )
    notify(
        "Link virou roteiro",
        icon="📥",
        run=short_id(run_id),
        de=candidate.origin,
        titulo=candidate.title[:80],
        chars=candidate.char_count,
        views=transcription.source.get("view_count"),
    )
    return "submitted"


async def handle_message(message: str) -> list[str]:
    """Trata uma mensagem do tópico. Uma URL de cada vez, de propósito.

    Sequencial e não concorrente porque o TikTok tem rate limit por IP: duas
    transcrições em paralelo derrubariam as duas. O ``sleep`` entre links é o
    mesmo freio que o ``sleep_requests`` do yt-dlp aplica dentro de um download.
    """
    urls = extract_urls(message)
    if not urls:
        log.info("inbox_message_without_url", message=message[:120])
        return []

    outcomes = []
    for index, url in enumerate(urls):
        if index:
            await asyncio.sleep(settings.CONFIG.inbox.delay_between_urls_seconds)
        try:
            outcomes.append(await handle_url(url))
        except Exception as exc:  # noqa: BLE001 — um link ruim não pode matar o laço
            log.error("inbox_url_failed", url=url, error=str(exc), exc_info=True)
            outcomes.append("error")
    return outcomes


async def inbox_loop() -> None:
    """Assina o stream do ntfy e trata cada mensagem que chega.

    A conexão é longa e **vai** cair — o ntfy.sh recicla conexões ociosas, e o
    laço trata isso como rotina, não como falha: reconectar em silêncio é o
    comportamento correto. Só uma falha repetida vira aviso, senão a queda normal
    de fim de semana encheria o celular de notificação sobre nada.

    O ntfy manda eventos ``keepalive`` e ``open`` no mesmo stream; só ``message``
    carrega texto de usuário.
    """
    url = settings.env.ntfy_inbox_url
    if not url:
        log.info("inbox_loop_disabled", reason="NTFY_INBOX_URL vazio")
        return

    stream_url = f"{url.rstrip('/')}/json"
    cfg = settings.CONFIG.inbox
    failures = 0
    log.info("inbox_loop_started", stream=stream_url)

    while True:
        try:
            timeout = httpx.Timeout(cfg.connect_timeout, read=None)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", stream_url) as response:
                    response.raise_for_status()
                    failures = 0
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            log.warning("inbox_bad_json", line=line[:120])
                            continue
                        if event.get("event") != "message":
                            continue
                        await handle_message(event.get("message") or "")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — o laço tem que sobreviver à rede
            failures += 1
            log.warning("inbox_stream_dropped", error=str(exc), failures=failures)
            if failures == cfg.failures_before_alert:
                notify(
                    "Caixa de entrada de links caiu",
                    level="error",
                    icon="📴",
                    erro=str(exc)[:160],
                    tentativas=failures,
                )
        await asyncio.sleep(cfg.reconnect_delay_seconds)
