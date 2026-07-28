import asyncio
import time
import uuid

from src.core import settings
from src.orchestrator.clients.http import request

_POLL_INTERVAL = 10
_POLL_TIMEOUT = 3600


class BlenderClient:
    def __init__(self):
        self._base = settings.CONFIG.services.blender_url

    async def create_video(
        self,
        video_file_key: str,
        music_key: str,
        voice_key: str,
        subtitle_key: str,
        card_key: str | None = None,
        hook_voice_key: str | None = None,
        hook_muted: bool = False,
    ) -> uuid.UUID:
        resp = await request(
            "POST",
            f"{self._base}/videos",
            timeout=30,
            json={
                "video_file_key": video_file_key,
                "music_key": music_key,
                "voice_key": voice_key,
                "subtitle_key": subtitle_key,
                "card_key": card_key,
                "hook_voice_key": hook_voice_key,
                "hook_muted": hook_muted,
            },
        )
        return uuid.UUID(resp.json()["id"])

    async def render_card(self, text: str, template: str, output_key: str) -> str:
        """Compose the comment card PNG. Returns the key it was written to.

        Synchronous on the worker's side (Pillow, not Blender), so there is no
        job to poll — the response already carries the finished object.
        """
        resp = await request(
            "POST",
            f"{self._base}/images/render",
            timeout=60,
            json={"template": template, "text": text, "output_key": output_key},
        )
        return resp.json()["output_key"]

    async def create_job(self, video_id: uuid.UUID, template_id: uuid.UUID) -> uuid.UUID:
        resp = await request(
            "POST",
            f"{self._base}/jobs",
            timeout=30,
            json={"video_id": str(video_id), "template_id": str(template_id)},
        )
        return uuid.UUID(resp.json()["id"])

    async def get_template_config(self, template_id: uuid.UUID) -> dict:
        """The template's parsed `template.json` (narration, timing, subtitles, ...)."""
        resp = await request("GET", f"{self._base}/templates/{template_id}/config", timeout=30)
        return resp.json()

    async def get_job_status(self, job_id: uuid.UUID) -> dict:
        resp = await request("GET", f"{self._base}/jobs/{job_id}", timeout=10)
        return resp.json()

    async def poll_job(
        self,
        job_id: uuid.UUID,
        timeout: int = _POLL_TIMEOUT,
        interval: int = _POLL_INTERVAL,
    ) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = await self.get_job_status(job_id)
            if data["status"] in ("completed", "failed"):
                return data
            await asyncio.sleep(interval)
        raise TimeoutError(f"render job {job_id} timed out after {timeout}s")
