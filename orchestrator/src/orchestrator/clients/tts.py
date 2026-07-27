from src.core import settings
from src.orchestrator.clients.http import request


class TTSClient:
    def __init__(self):
        self._base = settings.CONFIG.services.tts_url

    async def generate(self, text: str, run_id: str, part_number: int) -> tuple[str, str]:
        """Returns (audio_key, srt_key) of the generated files."""
        resp = await request(
            "POST",
            f"{self._base}/generate",
            timeout=300,
            json={"text": text, "run_id": run_id, "part_number": part_number},
        )
        data = resp.json()
        return data["audio_key"], data["srt_key"]
