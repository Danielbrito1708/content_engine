import httpx

from src.core import settings
from src.orchestrator.clients.http import request


class BufferQueueFull(Exception):
    """The poster has no slot: Buffer's queue is at its limit.

    Deliberately not a failure. Nothing is wrong with the run — its videos are
    rendered and waiting — so the worker leaves it in ``scheduling`` and retries
    later instead of burning an hour of LLM, speech and render work because the
    queue happened to be full at that minute.
    """

    def __init__(self, pending_count: int | None = None):
        self.pending_count = pending_count
        super().__init__(f"buffer queue full (pending={pending_count})")


class TikTokClient:
    def __init__(self):
        self._base = settings.CONFIG.services.tiktok_url

    async def schedule(self, video_key: str, classification: dict, part_number: int, series_id: str) -> dict:
        """Returns {"scheduled_at": "...", "buffer_update_id": "..."}"""
        try:
            resp = await request(
                "POST",
                f"{self._base}/schedule",
                timeout=30,
                json={
                    "video_key": video_key,
                    "classification": classification,
                    "part_number": part_number,
                    "series_id": series_id,
                },
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise BufferQueueFull(_pending_count(exc.response)) from exc
            raise
        return resp.json()


def _pending_count(response: httpx.Response) -> int | None:
    """Best-effort read of the poster's ``pending_count``; it is only a log detail."""
    try:
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001 — a malformed body must not mask the queue-full signal
        return None
    return detail.get("pending_count") if isinstance(detail, dict) else None
