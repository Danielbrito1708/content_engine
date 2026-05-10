import asyncio

from fastapi import APIRouter, HTTPException
from structlog import get_logger

from src.core import settings
from src.tts_service.audio.silence import remove_silence
from src.tts_service.schemas.generate import GenerateRequest, GenerateResponse
from src.tts_service.storage.client import upload_audio
from src.tts_service.tts.factory import get_tts_client

router = APIRouter()
log = get_logger(__name__)


@router.post("/generate", response_model=GenerateResponse, status_code=201)
async def generate(body: GenerateRequest) -> GenerateResponse:
    log.info("tts request", run_id=body.run_id, part=body.part_number, chars=len(body.text))

    client = get_tts_client()

    try:
        audio_bytes = await client.generate(body.text)
    except Exception as exc:
        log.error("tts generation failed", error=str(exc))
        raise HTTPException(status_code=502, detail=f"TTS error: {exc}")

    if settings.env.remove_silence:
        try:
            audio_bytes = await asyncio.to_thread(
                remove_silence,
                audio_bytes,
                settings.env.min_silence_ms,
                settings.env.silence_thresh_db,
                settings.env.silence_padding_ms,
            )
            log.debug("silence removed", size_kb=len(audio_bytes) // 1024)
        except Exception as exc:
            log.warning("silence removal failed, using original audio", error=str(exc))

    key = f"audio/{body.run_id}/part_{body.part_number}.mp3"
    bucket = settings.CONFIG.storage.bucket

    try:
        await upload_audio(bucket, key, audio_bytes)
    except Exception as exc:
        log.error("audio upload failed", key=key, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Upload error: {exc}")

    log.info("audio ready", key=key, size_kb=len(audio_bytes) // 1024)
    return GenerateResponse(audio_key=key)
