from dataclasses import dataclass

import httpx
import structlog

from src.core import settings

log = structlog.get_logger(__name__)


class TranscriptionError(RuntimeError):
    """A URL não virou texto.

    Vale a distinção que o cliente faz entre motivos, porque a resposta a cada um
    é diferente: rate limit do TikTok pede reenvio mais tarde, URL sem fala pede
    outro vídeo, e serviço fora do ar não pede nada de quem mandou o link.
    """

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Transcription:
    text: str
    char_count: int
    audio_duration: float
    source: dict

    @property
    def video_id(self) -> str:
        return str(self.source.get("video_id") or "")

    @property
    def title(self) -> str:
        return str(self.source.get("title") or "")


class TranscribeClient:
    """Fala com ``tts_service POST /transcribe``.

    O timeout é generoso e precisa ser: a transcrição roda a ~0.4x tempo real de
    CPU, então um vídeo de 3 minutos ocupa ~70s antes de responder qualquer
    coisa. Um timeout de client padrão cortaria toda transcrição real.
    """

    def __init__(self):
        self._base = settings.CONFIG.services.tts_url
        self._timeout = settings.CONFIG.inbox.transcribe_timeout

    async def transcribe(self, url: str) -> Transcription:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}/transcribe", json={"url": url})
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"tts_service inacessível: {exc}", retryable=True) from exc

        if resp.status_code == 422:
            raise TranscriptionError("o áudio não tem fala reconhecível")
        if resp.status_code >= 400:
            detail = resp.text[:200]
            # 502 do /transcribe é a plataforma recusando, não a URL sendo
            # inválida — e recusa de plataforma costuma passar na tentativa
            # seguinte, então marcar como retentável é o que evita descartar um
            # link bom por causa de rate limit.
            raise TranscriptionError(detail, retryable=resp.status_code >= 500)

        data = resp.json()
        return Transcription(
            text=data["text"],
            char_count=data["char_count"],
            audio_duration=data["audio_duration"],
            source=data.get("source") or {},
        )
