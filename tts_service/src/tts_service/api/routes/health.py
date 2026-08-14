from fastapi import APIRouter

from src.core import settings

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": settings.env.tts_provider,
        # `voice` is the fallback for a narrator with no gender; the other two are
        # what a request carrying one actually gets.
        "voice": settings.env.tts_voice,
        "voice_male": settings.env.tts_voice_male,
        "voice_female": settings.env.tts_voice_female,
        "rate": settings.env.tts_rate,
        # None means the output matches the source, so report that rather than a
        # number the service is not actually forcing.
        "bitrate": settings.env.audio_bitrate or "source",
        "sample_rate": settings.env.audio_sample_rate or "source",
        "normalize": settings.env.normalize_audio,
    }
