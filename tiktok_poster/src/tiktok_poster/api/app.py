from src.core import settings  # noqa: F401 — triggers bootstrap before any other import

from fastapi import FastAPI

from src.tiktok_poster.api.routes import health, schedule

app = FastAPI(title="tiktok_poster", version="0.1.0")

app.include_router(health.router)
app.include_router(schedule.router)
