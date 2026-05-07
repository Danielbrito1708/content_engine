from src.tts_service.tts.base import BaseTTSClient


class ElevenLabsClient(BaseTTSClient):
    async def generate(self, text: str) -> bytes:
        # TODO: implement when ElevenLabs is added
        raise NotImplementedError("ElevenLabs TTS not yet implemented")
