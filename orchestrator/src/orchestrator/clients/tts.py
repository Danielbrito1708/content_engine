import httpx

from src.core import settings


class TTSClient:
    def __init__(self):
        self._base = settings.CONFIG.services.tts_url

    async def generate(
        self,
        text: str,
        run_id: str,
        part_number: int,
        rate: str | None = None,
    ) -> tuple[str, str]:
        """Returns (audio_key, srt_key) of the generated files.

        `rate` comes from the template's `narration.rate`; omitted, the tts_service
        falls back to its own TTS_RATE.
        """
        payload: dict = {"text": text, "run_id": run_id, "part_number": part_number}
        if rate is not None:
            payload["rate"] = rate

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{self._base}/generate", json=payload)
            resp.raise_for_status()
        data = resp.json()
        return data["audio_key"], data["srt_key"]
