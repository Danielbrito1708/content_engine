import httpx

from src.core import settings


class TTSClient:
    def __init__(self):
        self._base = settings.CONFIG.services.tts_url

    async def generate(self, text: str, run_id: str, part_number: int) -> tuple[str, str]:
        """Returns (audio_key, srt_key) of the generated files."""
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{self._base}/generate",
                json={"text": text, "run_id": run_id, "part_number": part_number},
            )
            resp.raise_for_status()
        data = resp.json()
        return data["audio_key"], data["srt_key"]
