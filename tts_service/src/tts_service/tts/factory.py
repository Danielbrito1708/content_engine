from src.core import settings
from src.tts_service.tts.base import BaseTTSClient


def get_tts_client(rate: str | None = None, voice: str | None = None) -> BaseTTSClient:
    provider = settings.env.tts_provider

    if provider == "edge":
        from src.tts_service.tts.edge import EdgeTTSClient
        return EdgeTTSClient(rate=rate, voice=voice)

    if provider == "azure":
        from src.tts_service.tts.azure import AzureTTSClient
        return AzureTTSClient(rate=rate, voice=voice)

    if provider == "elevenlabs":
        from src.tts_service.tts.elevenlabs import ElevenLabsClient
        return ElevenLabsClient()

    raise ValueError(
        f"Unknown TTS_PROVIDER: {provider!r}. Use 'edge', 'azure' or 'elevenlabs'."
    )
