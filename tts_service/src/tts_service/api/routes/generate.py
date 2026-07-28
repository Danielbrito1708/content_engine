import asyncio

from fastapi import APIRouter, HTTPException
from structlog import get_logger

from src.core import settings
from src.tts_service.audio.postprocess import process_audio
from src.tts_service.audio.transcribe import transcribe_to_srt
from src.tts_service.schemas.generate import GenerateRequest, GenerateResponse
from src.tts_service.storage.client import upload_audio, upload_bytes
from src.tts_service.tts.factory import get_tts_client

router = APIRouter()
log = get_logger(__name__)


@router.post("/generate", response_model=GenerateResponse, status_code=201)
async def generate(body: GenerateRequest) -> GenerateResponse:
    rate = body.rate or settings.env.tts_rate
    log.info(
        "tts request",
        run_id=body.run_id,
        part=body.part_number,
        chars=len(body.text),
        rate=rate,
        rate_source="request" if body.rate else "env",
    )

    client = get_tts_client(rate=rate)

    try:
        audio_bytes = await client.generate(body.text)
    except Exception as exc:
        log.error("tts generation failed", error=str(exc))
        raise HTTPException(status_code=502, detail=f"TTS error: {exc}")

    if settings.env.remove_silence or settings.env.normalize_audio:
        try:
            audio_bytes = await asyncio.to_thread(
                process_audio,
                audio_bytes,
                trim_silence=settings.env.remove_silence,
                max_pause_ms=settings.env.max_pause_ms,
                silence_thresh_db=settings.env.silence_thresh_db,
                normalize=settings.env.normalize_audio,
                loudness_target_lufs=settings.env.loudness_target_lufs,
                bitrate=settings.env.audio_bitrate,
                sample_rate=settings.env.audio_sample_rate,
            )
            log.debug("audio processed", size_kb=len(audio_bytes) // 1024)
        except Exception as exc:
            log.warning("audio postprocessing failed, using original audio", error=str(exc))

    bucket = settings.CONFIG.storage.bucket
    audio_key = f"audio/{body.run_id}/part_{body.part_number}.mp3"

    try:
        await upload_audio(bucket, audio_key, audio_bytes)
    except Exception as exc:
        log.error("audio upload failed", key=audio_key, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Upload error: {exc}")

    log.info("audio ready", key=audio_key, size_kb=len(audio_bytes) // 1024)

    try:
        srt_bytes = await asyncio.to_thread(
            transcribe_to_srt,
            audio_bytes,
            settings.env.whisper_language,
            settings.env.whisper_model,
        )
        srt_key = f"subs/{body.run_id}/part_{body.part_number}.srt"
        await upload_bytes(bucket, srt_key, srt_bytes, "text/plain")
        log.info("srt ready", key=srt_key)
    except Exception as exc:
        log.error("transcription failed", error=str(exc))
        raise HTTPException(status_code=502, detail=f"Transcription error: {exc}")

    return GenerateResponse(audio_key=audio_key, srt_key=srt_key)
