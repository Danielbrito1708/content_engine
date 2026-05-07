from src.core import settings  # noqa: F401 — triggers bootstrap before any other import

from fastapi import FastAPI

from src.llm_service.api.routes import health, refine

app = FastAPI(title="llm_service", version="0.1.0")

app.include_router(health.router)
app.include_router(refine.router)
