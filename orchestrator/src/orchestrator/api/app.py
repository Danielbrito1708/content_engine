from src.core import settings  # noqa: F401 — triggers bootstrap before any other import

from fastapi import FastAPI

from src.orchestrator.api.routes import health, pipeline

app = FastAPI(title="orchestrator", version="0.1.0")

app.include_router(health.router)
app.include_router(pipeline.router)
