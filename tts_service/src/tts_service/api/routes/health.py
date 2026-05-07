import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    provider = os.environ.get("TTS_PROVIDER", "edge")
    voice = os.environ.get("TTS_VOICE", "pt-BR-ThalitaNeural")
    return {"status": "ok", "provider": provider, "voice": voice}
