import httpx

from src.core import settings


class TTSClient:
    def __init__(self):
        self._base = settings.CONFIG.services.tts_url

    async def generate(self, text: str, run_id: str, part_number: int) -> str:
        """Returns the MinIO key of the generated audio file."""
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self._base}/generate",
                json={"text": text, "run_id": run_id, "part_number": part_number},
            )
            resp.raise_for_status()
        return resp.json()["audio_key"]
