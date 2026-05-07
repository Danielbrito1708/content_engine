import uuid

import httpx

from src.core import settings


class BlenderClient:
    def __init__(self):
        self._base = settings.CONFIG.services.blender_url

    async def create_job(self, video_id: str, template_id: str) -> uuid.UUID:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/jobs",
                json={"video_id": video_id, "template_id": template_id},
            )
            resp.raise_for_status()
        return uuid.UUID(resp.json()["id"])

    async def get_job_status(self, job_id: uuid.UUID) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base}/jobs/{job_id}")
            resp.raise_for_status()
        return resp.json()
