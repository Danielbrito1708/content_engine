from src.core import settings  # triggers bootstrap before any other import

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.core.notify import notify, sender_loop
from src.orchestrator.api.routes import accounts, health, pipeline
from src.orchestrator.background_inbox import background_inbox_loop
from src.orchestrator.worker import maintenance_loop, recover_interrupted_runs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Reconcile before serving, then keep draining in the background.

    The recovery runs *before* the first request is accepted: until it has, the
    run table still claims work is in flight that no task is doing, and the
    scout would read that as occupied capacity.

    O ``sender_loop`` sobe **antes** da reconciliação: é ela que produz o
    primeiro aviso interessante do boot (quantos runs o restart derrubou), e sem
    consumidor esse aviso ficaria parado na fila até o primeiro evento seguinte.
    """
    notifier = asyncio.create_task(sender_loop())
    # A caixa de entrada de fundos é independente do resto do pipeline — o único
    # efeito dela é atualizar o manifesto de fundos, nunca criar um PipelineRun.
    inbox_enabled = bool(
        settings.CONFIG.background_inbox.enabled and settings.env.ntfy_background_inbox_url
    )
    notify(
        "orchestrator no ar",
        icon="🟢",
        caixa_de_fundos="ligada" if inbox_enabled else "desligada",
    )
    await recover_interrupted_runs()
    task = asyncio.create_task(maintenance_loop())
    inbox = asyncio.create_task(background_inbox_loop()) if inbox_enabled else None
    yield
    for running in (task, inbox):
        if running is not None:
            running.cancel()
    notifier.cancel()


app = FastAPI(title="orchestrator", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(pipeline.router)
app.include_router(accounts.router)
