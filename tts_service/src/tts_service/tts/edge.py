import edge_tts

from src.core import settings
from src.tts_service.tts.base import BaseTTSClient


class EdgeTTSClient(BaseTTSClient):
    def __init__(self, voice: str | None = None):
        self._voice = voice or settings.env.tts_voice

    async def generate(self, text: str) -> bytes:
        communicate = edge_tts.Communicate(text, self._voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)
