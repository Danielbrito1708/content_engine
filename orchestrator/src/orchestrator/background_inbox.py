"""Caixa de entrada de fundos: um link de vídeo compartilhado no celular vira
clipe na biblioteca sob demanda, sem precisar de terminal.

Mesmo padrão do `content_scout/inbox.py` — ntfy porque já está no ar, já tem o
app no celular e aparece na aba de compartilhar do Android —, adaptado para
outro alvo: em vez de virar roteiro, o link vira entradas novas no
`background_manifest.json` (`backgrounds.py`, `background_source.py`).

**Nenhum vídeo é baixado aqui.** Só os metadados (duração, id) são lidos, do
mesmo jeito que `scripts/build_background_manifest.py` lê a playlist inteira
sem baixar nada — o clipe de fato só vira arquivo quando um render o sortear
(`ensure_available`). Isto é o caminho de **um vídeo por vez**; uma playlist
inteira continua sendo o script manual.
"""

import asyncio
import json
import re

import httpx
import structlog

from src.core import settings
from src.core.notify import notify
from src.orchestrator.backgrounds import plan_segments, segment_key
from src.orchestrator.storage.client import get_bytes, upload_bytes

log = structlog.get_logger(__name__)

#: URLs dentro da mensagem. O app do ntfy manda o texto do compartilhamento
#: inteiro, que pode vir com texto em volta do link.
_URL_RE = re.compile(r"https?://[^\s<>\"]+")


def extract_urls(message: str) -> list[str]:
    """Todas as URLs de uma mensagem, na ordem, sem repetir."""
    seen: set[str] = set()
    urls = []
    for match in _URL_RE.findall(message or ""):
        url = match.rstrip(".,;)]}")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _fetch_metadata(url: str) -> dict:
    from yt_dlp import YoutubeDL

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


async def _load_manifest(bucket: str, path: str, segundos: int) -> dict:
    """O manifesto atual, ou um vazio se ele ainda não existe/está ilegível.

    Mesma postura de `background_source.py`: manifesto ausente é estado normal
    (biblioteca nova, ou ainda não regenerada), não erro.
    """
    try:
        data = await get_bytes(bucket, path)
        manifest = json.loads(data)
    except Exception:  # noqa: BLE001 — ausência/objeto corrompido é o caso normal
        manifest = {"version": 1, "segment_seconds": segundos, "clips": []}
    manifest.setdefault("clips", [])
    return manifest


async def handle_url(url: str) -> str:
    """Processa um link. Devolve o desfecho, para o log e a notificação."""
    cfg = settings.CONFIG
    prefix = str(cfg.template.background_prefix)
    manifest_path = str(cfg.template.background_manifest)
    bucket = str(cfg.storage.bucket)
    segundos = int(cfg.backgrounds.segment_seconds)
    min_tail = int(cfg.backgrounds.min_tail_seconds)

    try:
        info = await asyncio.get_event_loop().run_in_executor(None, _fetch_metadata, url)
    except Exception as exc:  # noqa: BLE001 — site fora do ar, vídeo removido, etc.
        log.warning("background_inbox_metadata_failed", url=url, error=str(exc))
        notify(
            "Não consegui ler o vídeo",
            level="warning",
            icon="⚠️",
            link=url[:120],
            erro=str(exc)[:160],
        )
        return "metadata_failed"

    video_id = str(info.get("id") or "")
    duration = info.get("duration")
    title = str(info.get("title") or video_id or url)
    if not video_id or not duration:
        log.info("background_inbox_no_duration", url=url)
        notify("Vídeo sem duração legível", level="warning", icon="⚠️", link=url[:120])
        return "no_duration"

    starts = plan_segments(float(duration), segundos, min_tail)
    if not starts:
        log.info("background_inbox_too_short", url=url, duration=duration)
        notify(
            "Vídeo curto demais para virar fundo",
            level="warning",
            icon="✂️",
            titulo=title[:80],
            duracao=int(duration),
        )
        return "too_short"

    manifest = await _load_manifest(bucket, manifest_path, segundos)
    existentes = {str(c.get("key")) for c in manifest["clips"]}
    novos = [
        {"key": chave, "video_id": video_id, "start": start}
        for start in starts
        if (chave := segment_key(prefix, video_id, start)) not in existentes
    ]

    if not novos:
        log.info("background_inbox_duplicate", url=url, video_id=video_id)
        notify("Vídeo já estava no catálogo", level="info", icon="🔁", titulo=title[:80])
        return "duplicate"

    manifest["clips"].extend(novos)
    await upload_bytes(
        bucket,
        manifest_path,
        json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )

    log.info("background_inbox_added", url=url, video_id=video_id, clips=len(novos))
    notify(
        "Fundo novo no catálogo",
        icon="🎞️",
        titulo=title[:80],
        clipes=len(novos),
        video_id=video_id,
    )
    return "submitted"


async def handle_message(message: str) -> list[str]:
    """Trata uma mensagem do tópico. Todas as URLs da mensagem, sem pausa entre
    elas — ao contrário da caixa de entrada de roteiros, nada aqui bate numa
    API com rate limit por IP; é metadado do yt-dlp e upload para o próprio
    bucket."""
    urls = extract_urls(message)
    if not urls:
        log.info("background_inbox_message_without_url", message=message[:120])
        return []

    outcomes = []
    for url in urls:
        try:
            outcomes.append(await handle_url(url))
        except Exception as exc:  # noqa: BLE001 — um link ruim não pode matar o laço
            log.error("background_inbox_url_failed", url=url, error=str(exc), exc_info=True)
            outcomes.append("error")
    return outcomes


async def background_inbox_loop() -> None:
    """Assina o stream do ntfy e trata cada mensagem que chega.

    A conexão é longa e **vai** cair — o ntfy.sh recicla conexões ociosas, e o
    laço trata isso como rotina: reconectar em silêncio é o comportamento
    correto. Só uma falha repetida vira aviso.
    """
    url = settings.env.ntfy_background_inbox_url
    if not url:
        log.info("background_inbox_loop_disabled", reason="NTFY_BACKGROUND_INBOX_URL vazio")
        return

    stream_url = f"{url.rstrip('/')}/json"
    cfg = settings.CONFIG.background_inbox
    failures = 0
    log.info("background_inbox_loop_started", stream=stream_url)

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
                            log.warning("background_inbox_bad_json", line=line[:120])
                            continue
                        if event.get("event") != "message":
                            continue
                        await handle_message(event.get("message") or "")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — o laço tem que sobreviver à rede
            failures += 1
            log.warning("background_inbox_stream_dropped", error=str(exc), failures=failures)
            if failures == cfg.failures_before_alert:
                notify(
                    "Caixa de entrada de fundos caiu",
                    level="error",
                    icon="📴",
                    erro=str(exc)[:160],
                    tentativas=failures,
                )
        await asyncio.sleep(cfg.reconnect_delay_seconds)
