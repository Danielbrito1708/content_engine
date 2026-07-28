from src.core import settings  # noqa: F401 — triggers bootstrap before any other import

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.orchestrator.api.routes import health, pipeline
from src.orchestrator.worker import maintenance_loop, recover_interrupted_runs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Reconcile before serving, then keep draining in the background.

    The recovery runs *before* the first request is accepted: until it has, the
    run table still claims work is in flight that no task is doing, and the
    scout would read that as occupied capacity.
    """
    await recover_interrupted_runs()
    task = asyncio.create_task(maintenance_loop())
    yield
    task.cancel()


app = FastAPI(title="orchestrator", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(pipeline.router)
