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
        # None means the output matches the source, so report that rather than a
        # number the service is not actually forcing.
        "bitrate": settings.env.audio_bitrate or "source",
        "sample_rate": settings.env.audio_sample_rate or "source",
        "normalize": settings.env.normalize_audio,
    }
