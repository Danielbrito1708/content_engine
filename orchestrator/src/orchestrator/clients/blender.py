import asyncio
import time
import uuid

import httpx

from src.core import settings

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
    ) -> uuid.UUID:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/videos",
                json={
                    "video_file_key": video_file_key,
                    "music_key": music_key,
                    "voice_key": voice_key,
                    "subtitle_key": subtitle_key,
                },
            )
            resp.raise_for_status()
        return uuid.UUID(resp.json()["id"])

    async def create_job(self, video_id: uuid.UUID, template_id: uuid.UUID) -> uuid.UUID:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/jobs",
                json={"video_id": str(video_id), "template_id": str(template_id)},
            )
            resp.raise_for_status()
        return uuid.UUID(resp.json()["id"])

    async def get_job_status(self, job_id: uuid.UUID) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base}/jobs/{job_id}")
            resp.raise_for_status()
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
