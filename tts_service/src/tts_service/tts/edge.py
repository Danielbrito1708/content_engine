import os

import edge_tts

from src.tts_service.tts.base import BaseTTSClient

_DEFAULT_VOICE = "pt-BR-ThalitaNeural"


class EdgeTTSClient(BaseTTSClient):
    def __init__(self, voice: str | None = None):
        self._voice = voice or os.environ.get("TTS_VOICE", _DEFAULT_VOICE)

    async def generate(self, text: str) -> bytes:
        communicate = edge_tts.Communicate(text, self._voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)
