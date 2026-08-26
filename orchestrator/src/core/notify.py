"""Notificação de eventos de operação: WhatsApp, webhook e dead-man's switch.

Existe porque o pipeline roda sozinho, 24h, sem ninguém olhando. Sem isto um run
pode falhar — ou, pior, todos os serviços podem responder ``200`` e simplesmente
nenhum vídeo sair — e a primeira pessoa a descobrir é quem for conferir o perfil
dias depois.

**Duas garantias, e as duas são a razão de o módulo existir separado:**

1. **Nunca levanta exceção.** ``notify()`` é envolvido inteiro num ``try``. Um
   monitoramento que derruba o run que ele monitora é pior que monitoramento
   nenhum — a falha que ele criaria seria justamente a que ninguém consegue
   diagnosticar, porque o canal de diagnóstico é ele.
2. **Nunca bloqueia quem chama.** ``notify()`` é síncrono e só enfileira; quem
   fala com a rede é o ``sender_loop`` no fundo. Um envio leva ~1s, e com uma
   dúzia de eventos por run um envio síncrono somaria minutos ao pipeline por
   causa de uma mensagem de celular.

**Credenciais vêm do ambiente, comportamento vem do ``config.ini``.** Chave de
API não entra em arquivo versionado; nível de verbosidade e cadência não são
segredo e ficam onde se lê o resto da configuração. É também o que mantém este
arquivo idêntico entre os serviços — ele não conhece o ``EnvSettings`` de
nenhum deles, então é copiável para qualquer serviço novo sem edição.

Env vars (todas opcionais — ausente significa destino desligado):

- ``CALLMEBOT_PHONE`` / ``CALLMEBOT_APIKEY`` — WhatsApp via CallMeBot
- ``NOTIFY_WEBHOOK_URL`` — POST, escape hatch para ntfy/Discord/WAHA. O corpo
  depende de ``[monitoring] webhook_format``: ``json`` manda ``{"text": ...}``
  (Slack, Discord via proxy), ``text`` manda a mensagem crua no corpo (ntfy, que
  em JSON mostraria o literal ``{"text": ...}`` na tela do celular).
- ``HEALTHCHECK_{CHECK}_URL`` — destino do ping de ``ping("{check}")``

Config em ``[monitoring]``: ``enabled``, ``level``, ``min_interval_seconds``,
``webhook_format``.
"""

import asyncio
import os
import re
from typing import Any
from urllib.parse import quote

import httpx
from structlog import get_logger

from src.core import settings

log = get_logger(__name__)

#: Ordem de severidade. O corte em ``[monitoring] level`` descarta tudo abaixo.
LEVELS: dict[str, int] = {"debug": 10, "info": 20, "warning": 30, "error": 40}

#: Nível assumido quando o config não diz nada: tudo passa. É o começo de
#: operação — a pergunta a responder é "o que este sistema faz quando roda
#: sozinho", e ela só se responde vendo o fluxo inteiro. Subir para ``info``
#: depois é uma linha no ``config.ini``, sem tocar em código.
DEFAULT_LEVEL = "debug"

#: Teto do corpo da mensagem. O CallMeBot é um GET, então a mensagem inteira vai
#: na query string e um erro do LLM com stack trace de 4 KB estouraria a URL.
MAX_TEXT = 800

#: Fila de saída. Cheia, a mensagem mais nova é descartada com um log — perder
#: notificação é aceitável, travar o pipeline atrás dela não é.
QUEUE_MAX = 500

SEND_TIMEOUT = 15.0
#: Duas tentativas, não três: a mensagem já é redundante com o log estruturado, e
#: insistir num destino fora do ar só atrasa as mensagens seguintes da fila.
SEND_ATTEMPTS = 2
SEND_BACKOFF = 1.0

CALLMEBOT_DEFAULT_URL = "https://api.callmebot.com/whatsapp.php"

#: Formato do corpo do webhook quando o ``config.ini`` não diz nada. ``json``
#: preserva o contrato antigo — mudar o default quebraria em silêncio qualquer
#: destino que já esteja lendo ``{"text": ...}``.
DEFAULT_WEBHOOK_FORMAT = "json"

#: Marcador de sucesso na resposta do CallMeBot, que responde ``200`` **mesmo
#: quando recusa** — cota esgotada, apikey inválida, número não autorizado — e
#: diz isso só no corpo. Em 16/08/2026 a cota grátis zerou e o monitoramento
#: morreu em silêncio exatamente por aqui: o log registrou ``200 OK`` para dias
#: de mensagens que nunca saíram, e a falha foi descoberta por não ter chegado
#: mensagem nenhuma no celular.
#:
#: A checagem é pelo **sucesso**, não por uma lista de textos de erro conhecidos:
#: um modo de recusa novo cai no ``else`` e vira aviso, em vez de passar batido
#: por não estar na lista. É a escolha que troca o risco de um alarme falso no
#: log pelo risco de outro silêncio — e o silêncio é o que já custou caro.
CALLMEBOT_OK_MARKER = "queued"

#: Tamanho do trecho da recusa que vai para o log. A resposta é HTML e o motivo
#: legível ("You have 0 messages left") cabe folgado nisto.
REJECTION_REASON_CHARS = 200

_HTML_TAG = re.compile(r"<[^>]+>")

_queue: asyncio.Queue[str] | None = None


def _cfg(key: str, default: Any) -> Any:
    """Uma chave de ``[monitoring]``, ou o default se a seção não existir.

    Tolerante de propósito: um serviço cujo ``config.ini`` ainda não tem a seção
    continua subindo, apenas silencioso.
    """
    section = getattr(settings.CONFIG, "monitoring", None)
    if section is None:
        return default
    value = getattr(section, key, default)
    return default if value is None else value


def _destinations() -> tuple[str, str, str]:
    """(phone, apikey, webhook) do ambiente. Vazio significa destino desligado."""
    return (
        os.environ.get("CALLMEBOT_PHONE", "").strip(),
        os.environ.get("CALLMEBOT_APIKEY", "").strip(),
        os.environ.get("NOTIFY_WEBHOOK_URL", "").strip(),
    )


def _enabled() -> bool:
    """Ligado no config **e** com pelo menos um destino configurado.

    A segunda metade importa: sem ela, um ambiente sem credencial nenhuma —
    a suíte de testes, uma máquina de desenvolvimento — encheria a fila com
    mensagens que ninguém drenaria.
    """
    if not bool(_cfg("enabled", True)):
        return False
    phone, apikey, webhook = _destinations()
    return bool((phone and apikey) or webhook)


def _passes_level(level: str) -> bool:
    threshold = LEVELS.get(str(_cfg("level", DEFAULT_LEVEL)).strip().lower(), LEVELS["debug"])
    return LEVELS.get(level, LEVELS["info"]) >= threshold


def _get_queue() -> asyncio.Queue[str]:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=QUEUE_MAX)
    return _queue


def reset() -> None:
    """Descarta a fila. Só para testes — cada um começa do zero."""
    global _queue
    _queue = None


def short_id(value: Any) -> str:
    """Os 8 primeiros caracteres de um UUID.

    Um UUID inteiro ocupa metade da largura da tela de um celular e não é lido
    por ninguém; 8 caracteres bastam para casar a mensagem com a linha do log ou
    com o ``GET /pipeline/{id}``.
    """
    return str(value)[:8]


def format_message(text: str, *, icon: str = "•", **fields: Any) -> str:
    """Uma mensagem legível num celular: título com ícone, detalhes numa linha.

    Campos ``None`` ou vazios somem em vez de virar ``campo: None`` — quase todo
    evento tem campos opcionais, e mostrá-los vazios encheria a mensagem de ruído
    exatamente onde o espaço é curto.
    """
    details = " · ".join(
        f"{key}: {value}" for key, value in fields.items() if value is not None and value != ""
    )
    body = f"{icon} {text}"
    if details:
        body = f"{body}\n{details}"
    return body[:MAX_TEXT]


def notify(text: str, *, level: str = "info", icon: str = "•", **fields: Any) -> None:
    """Enfileira um evento. Síncrono, não bloqueia, nunca levanta.

    Síncrono de propósito: sem ``await`` no ponto de chamada não há como um
    enganche de notificação virar um ponto de suspensão no meio de uma transação,
    e não há como alguém acidentalmente esperar pela rede dentro do pipeline.
    """
    try:
        if not _enabled() or not _passes_level(level):
            return
        try:
            _get_queue().put_nowait(format_message(text, icon=icon, **fields))
        except asyncio.QueueFull:
            log.warning("notify_queue_full", dropped=text)
    except Exception as exc:  # noqa: BLE001 — o monitor jamais derruba o monitorado
        log.warning("notify_failed", error=str(exc), text=text)


async def ping(check: str, *, fail: bool = False) -> None:
    """Pinga um dead-man's switch externo (Healthchecks.io e compatíveis).

    Separado de ``notify`` porque responde a outra pergunta. A notificação diz
    "aconteceu isto"; o ping diz "eu ainda estou aqui" — e o alerta dele nasce do
    **silêncio**, do outro lado, que é a única forma de detectar uma máquina que
    morreu sem conseguir reportar a própria morte.

    Destino em ``HEALTHCHECK_{CHECK}_URL``. Sem a var, é no-op.
    """
    url = os.environ.get(f"HEALTHCHECK_{check.upper()}_URL", "").strip()
    if not url:
        return
    if fail:
        url = f"{url.rstrip('/')}/fail"
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
            await client.get(url)
        log.debug("healthcheck_pinged", check=check, fail=fail)
    except Exception as exc:  # noqa: BLE001 — mesma regra do notify
        log.warning("healthcheck_ping_failed", check=check, error=str(exc))


def strip_html(body: str) -> str:
    """Texto legível de uma resposta HTML, em uma linha. Puro.

    Existe porque o CallMeBot devolve o motivo da recusa embrulhado em ``<p>`` e
    ``<b>``: sem isto o log carregaria a marcação e o motivo — a única coisa que
    interessa ali — ficaria mais difícil de ler do que o problema que descreve.
    """
    return " ".join(_HTML_TAG.sub(" ", body).split())


def callmebot_accepted(body: str) -> bool:
    """Se o CallMeBot aceitou a mensagem, lido no corpo — o status é sempre 200."""
    return CALLMEBOT_OK_MARKER in body.lower()


async def _send(method: str, url: str, **kwargs: Any) -> httpx.Response | None:
    """A resposta de um envio bem-sucedido, ou ``None``. Nunca levanta.

    Devolve a resposta inteira, e não um ``bool``, porque status ``2xx`` não é o
    mesmo que mensagem entregue: quem chama pode precisar ler o corpo para saber
    se o destino de fato aceitou.
    """
    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
                response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            if attempt >= SEND_ATTEMPTS:
                log.warning("notify_send_failed", error=str(exc))
                return None
            await asyncio.sleep(SEND_BACKOFF)
    return None


async def _deliver(message: str) -> None:
    phone, apikey, webhook = _destinations()

    if phone and apikey:
        base = os.environ.get("CALLMEBOT_BASE_URL", "").strip() or CALLMEBOT_DEFAULT_URL
        url = f"{base}?phone={quote(phone)}&apikey={quote(apikey)}&text={quote(message)}"
        response = await _send("GET", url)
        if response is not None and not callmebot_accepted(response.text):
            log.warning(
                "notify_rejected",
                destination="callmebot",
                reason=strip_html(response.text)[:REJECTION_REASON_CHARS],
            )

    if webhook:
        if str(_cfg("webhook_format", DEFAULT_WEBHOOK_FORMAT)).strip().lower() == "text":
            await _send(
                "POST",
                webhook,
                content=message.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
        else:
            await _send("POST", webhook, json={"text": message})


async def sender_loop() -> None:
    """Drena a fila, uma mensagem por vez, espaçadas. Sobe no ``lifespan``.

    O espaçamento não é educação com o servidor: o CallMeBot recusa rajadas, e
    uma rajada é exatamente o que um run produz — refino, gancho, card e depois
    TTS e render de cada parte, tudo em poucos segundos. Sem o espaço, a metade
    do fim de cada run é a que se perde.

    Sobrevive a qualquer falha de envio pelo mesmo motivo do ``maintenance_loop``:
    a noite sem ninguém olhando é justamente quando ele não pode parar.
    """
    interval = float(_cfg("min_interval_seconds", 3))
    queue = _get_queue()
    log.info("notify_sender_started", min_interval_seconds=interval)

    while True:
        message = await queue.get()
        try:
            await _deliver(message)
        except Exception as exc:  # noqa: BLE001 — o loop tem que passar a noite
            log.warning("notify_deliver_failed", error=str(exc))
        finally:
            queue.task_done()
        await asyncio.sleep(interval)
