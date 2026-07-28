from src.core import settings
from src.orchestrator.clients.http import request


class TTSClient:
    def __init__(self):
        self._base = settings.CONFIG.services.tts_url

    async def generate(
        self,
        text: str,
        run_id: str,
        part_number: int = 1,
        rate: str | None = None,
        label: str | None = None,
    ) -> tuple[str, str]:
        """Returns (audio_key, srt_key) of the generated files.

        `rate` comes from the template's `narration.rate`; omitted, the tts_service
        falls back to its own TTS_RATE.

        ``label`` nomeia o arquivo quando o áudio não é uma parte do roteiro —
        é assim que a frase gancho vira ``audio/{run_id}/hook.mp3``.
        """
        payload: dict = {"text": text, "run_id": run_id, "part_number": part_number}
        if rate is not None:
            payload["rate"] = rate
        if label:
            payload["label"] = label

        resp = await request("POST", f"{self._base}/generate", timeout=300, json=payload)
        data = resp.json()
        return data["audio_key"], data["srt_key"]
