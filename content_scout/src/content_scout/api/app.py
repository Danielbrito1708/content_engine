from src.core import settings  # noqa: F401 — triggers bootstrap before any other import

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.content_scout.api.routes import health, scout
from src.content_scout.inbox import inbox_loop
from src.content_scout.scout import scout_loop
from src.core.notify import notify, sender_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    notifier = asyncio.create_task(sender_loop())
    task = None
    if settings.env.scout_enabled:
        task = asyncio.create_task(scout_loop())
    # A caixa de entrada é independente do `scout_enabled`: os dois laços fazem
    # coisas diferentes, e desligar a busca automática para publicar só o que se
    # escolhe à mão é um modo de operação legítimo — na verdade é o mais provável
    # de todos, num fim de semana em que a fila do Buffer está apertada.
    inbox = None
    if settings.CONFIG.inbox.enabled and settings.env.ntfy_inbox_url:
        inbox = asyncio.create_task(inbox_loop())
    # O estado dos laços vai na mensagem porque `SCOUT_ENABLED=false` é silencioso
    # por natureza: o serviço sobe, responde `/health`, e simplesmente nunca
    # busca nada. Ler isso no boot é mais barato que descobrir dias depois. Vale
    # igual para a caixa de entrada, cujo silêncio é ainda mais fácil de
    # confundir com "ninguém mandou link".
    notify(
        "content_scout no ar",
        icon="🟢",
        loop="ligado" if task else "desligado",
        caixa_de_entrada="ligada" if inbox else "desligada",
    )
    yield
    for running in (task, inbox):
        if running is not None:
            running.cancel()
    notifier.cancel()


app = FastAPI(title="content_scout", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(scout.router)
