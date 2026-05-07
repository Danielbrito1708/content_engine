import httpx

from src.core import settings


class TikTokClient:
    def __init__(self):
        self._base = settings.CONFIG.services.tiktok_url

    async def schedule(self, video_key: str, classification: dict, part_number: int, series_id: str) -> dict:
        """Returns {"scheduled_at": "...", "tiktok_video_id": "..."}"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/schedule",
                json={
                    "video_key": video_key,
                    "classification": classification,
                    "part_number": part_number,
                    "series_id": series_id,
                },
            )
            resp.raise_for_status()
        return resp.json()
