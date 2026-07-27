from fastapi import APIRouter

from src.core import settings

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": settings.env.tts_provider,
        "voice": settings.env.tts_voice,
        "rate": settings.env.tts_rate,
        "bitrate": settings.env.audio_bitrate,
        "sample_rate": settings.env.audio_sample_rate,
        "normalize": settings.env.normalize_audio,
    }
