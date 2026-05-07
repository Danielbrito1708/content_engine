from fastapi import APIRouter

from src.core import settings

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "provider": settings.env.tts_provider, "voice": settings.env.tts_voice}
