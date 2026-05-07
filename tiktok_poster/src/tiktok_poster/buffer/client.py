import os
from datetime import datetime

import httpx

_BASE = "https://api.bufferapp.com/1"


class BufferClient:
    def __init__(self):
        self._token = os.environ["BUFFER_ACCESS_TOKEN"]
        self._profile_id = os.environ["BUFFER_PROFILE_ID"]

    def _params(self, extra: dict | None = None) -> dict:
        p = {"access_token": self._token}
        if extra:
            p.update(extra)
        return p

    async def get_pending_posts(self) -> list[dict]:
        """Returns all pending (scheduled) posts for the configured profile."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE}/profiles/{self._profile_id}/updates/pending.json",
                params={"access_token": self._token},
            )
            resp.raise_for_status()
        return resp.json().get("updates", [])

    async def create_post(self, video_url: str, caption: str, scheduled_at: datetime) -> dict:
        """Schedules a video post on TikTok via Buffer."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_BASE}/updates/create.json",
                data={
                    "profile_ids[]": self._profile_id,
                    "text": caption,
                    "media[link]": video_url,
                    "scheduled_at": scheduled_at.isoformat(),
                    "access_token": self._token,
                },
            )
            resp.raise_for_status()
        return resp.json()

    async def verify_connection(self) -> bool:
        """Checks if the Buffer token and profile are valid."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{_BASE}/profiles/{self._profile_id}.json",
                    params={"access_token": self._token},
                )
            return resp.status_code == 200
        except Exception:
            return False
