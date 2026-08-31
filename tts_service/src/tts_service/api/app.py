from src.core import settings  # noqa: F401 — triggers bootstrap before any other import

from fastapi import FastAPI

from src.tts_service.api.routes import generate, health, transcribe

app = FastAPI(title="tts_service", version="0.1.0")

app.include_router(health.router)
app.include_router(generate.router)
app.include_router(transcribe.router)
