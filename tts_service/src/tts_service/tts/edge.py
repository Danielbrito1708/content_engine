import edge_tts

from src.core import settings
from src.tts_service.tts.base import BaseTTSClient


class EdgeTTSClient(BaseTTSClient):
    def __init__(self, voice: str | None = None, rate: str | None = None):
        self._voice = voice or settings.env.tts_voice
        self._rate = rate or settings.env.tts_rate

    async def generate(self, text: str) -> bytes:
        communicate = edge_tts.Communicate(text, self._voice, rate=self._rate)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)
