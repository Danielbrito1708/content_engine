import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from structlog import get_logger

from src.core import settings
from src.tts_service.audio.download import DownloadError, download_audio
from src.tts_service.audio.transcribe import transcribe_file_to_text
from src.tts_service.schemas.transcribe import (
    TranscribeRequest,
    TranscribeResponse,
    TranscribeSource,
)

router = APIRouter()
log = get_logger(__name__)


def _download_and_transcribe(url: str) -> tuple[str, float, dict]:
    """Baixa e transcreve num diretório temporário. Bloqueante de propósito.

    Roda inteiro dentro de uma thread (ver a chamada abaixo) porque as duas
    metades bloqueiam: o yt-dlp faz I/O síncrono e o whisper queima CPU. O
    temporário some junto com o bloco — o áudio não interessa depois que virou
    texto, e um MP4 de TikTok por link acumularia rápido.
    """
    cfg = settings.CONFIG.download
    with tempfile.TemporaryDirectory(prefix="transcribe_") as tmp:
        media = download_audio(
            url,
            Path(tmp),
            impersonate=cfg.impersonate,
            api_hostname=cfg.api_hostname,
            sleep_requests=cfg.sleep_requests,
            retries=cfg.retries,
            max_duration_seconds=cfg.max_duration_seconds,
        )
        text, audio_duration = transcribe_file_to_text(
            str(media.path),
            settings.env.whisper_language,
            settings.env.whisper_transcribe_model,
        )
    return text, audio_duration, media.as_metadata()


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(body: TranscribeRequest) -> TranscribeResponse:
    """URL de vídeo → texto corrido, para o refino reescrever.

    Vive no ``tts_service`` porque o serviço já tem as duas peças caras na
    imagem: ffmpeg e o faster-whisper. Um serviço novo para isso duplicaria
    ambas, e o ``content_scout``, que é quem consome, não tem nenhuma das duas.

    A resposta **não** vai para o pipeline direto. Quem chama decide o que fazer
    com o texto — este endpoint transcreve e nada mais, para que o mesmo caminho
    sirva a conferir uma transcrição sem produzir vídeo nenhum.
    """
    log.info("transcribe requested", url=body.url)

    try:
        text, audio_duration, source = await asyncio.to_thread(
            _download_and_transcribe, body.url
        )
    except DownloadError as exc:
        # 502 e não 400: a URL do usuário está bem formada, quem recusou foi a
        # plataforma. Distinguir isso importa porque a resposta ao rate limit é
        # tentar de novo mais tarde, e a resposta a uma URL inválida não é.
        log.warning("download failed", url=body.url, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Download error: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.error("transcription failed", url=body.url, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Transcription error: {exc}")

    # `.strip()` e não `not text`: o transcritor já devolve texto limpo, mas a
    # diferença entre "" e " " não pode decidir se um run de roteiro em branco
    # entra na fila.
    if not text.strip():
        # Áudio sem fala é um resultado, não um erro de infraestrutura — mas
        # devolver 200 com texto vazio faria o chamador criar um run de roteiro
        # em branco, que só falharia lá na frente e mais caro.
        log.warning("empty transcription", url=body.url, duration=audio_duration)
        raise HTTPException(
            status_code=422, detail="Transcrição vazia: o áudio não tem fala reconhecível"
        )

    log.info(
        "transcription ready",
        url=body.url,
        chars=len(text),
        audio_duration=round(audio_duration),
        views=source.get("view_count"),
    )
    return TranscribeResponse(
        text=text,
        char_count=len(text),
        audio_duration=audio_duration,
        source=TranscribeSource(**source),
    )
