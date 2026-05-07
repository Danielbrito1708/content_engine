from src.core import settings
from src.tts_service.tts.base import BaseTTSClient


def get_tts_client() -> BaseTTSClient:
    provider = settings.env.tts_provider

    if provider == "edge":
        from src.tts_service.tts.edge import EdgeTTSClient
        return EdgeTTSClient()

    if provider == "elevenlabs":
        from src.tts_service.tts.elevenlabs import ElevenLabsClient
        return ElevenLabsClient()

    raise ValueError(f"Unknown TTS_PROVIDER: {provider!r}. Use 'edge' or 'elevenlabs'.")
