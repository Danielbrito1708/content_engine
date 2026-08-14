from src.core import settings  # noqa: F401 — triggers bootstrap before any other import

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.content_scout.api.routes import health, scout
from src.content_scout.scout import scout_loop
from src.core.notify import notify, sender_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    notifier = asyncio.create_task(sender_loop())
    task = None
    if settings.env.scout_enabled:
        task = asyncio.create_task(scout_loop())
    # O estado do loop vai na mensagem porque `SCOUT_ENABLED=false` é silencioso
    # por natureza: o serviço sobe, responde `/health`, e simplesmente nunca
    # busca nada. Ler isso no boot é mais barato que descobrir dias depois.
    notify("content_scout no ar", icon="🟢", loop="ligado" if task else "desligado")
    yield
    if task is not None:
        task.cancel()
    notifier.cancel()


app = FastAPI(title="content_scout", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(scout.router)
