from src.core import settings  # noqa: F401 — triggers bootstrap before any other import

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.content_scout.api.routes import health, scout
from src.content_scout.scout import scout_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = None
    if settings.env.scout_enabled:
        task = asyncio.create_task(scout_loop())
    yield
    if task is not None:
        task.cancel()


app = FastAPI(title="content_scout", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(scout.router)
