import uuid

import httpx

from src.core import settings

# A run in any of these states still owes the Buffer queue a slot. Anything else
# has either landed or died, and no longer counts against the backlog.
ACTIVE_STATUSES = {"pending", "refining", "refined", "processing", "scheduling"}


class OrchestratorClient:
    def __init__(self):
        self._base = settings.CONFIG.services.orchestrator_url

    async def create_pipeline(
        self, script: str, metadata: dict, account_id: str | None = None
    ) -> uuid.UUID:
        payload = {"script": script, "metadata": metadata}
        # Omitted, not null, when there is no account — same convention the
        # orchestrator itself uses toward tiktok_poster (template_id, youtube_title).
        if account_id is not None:
            payload["account_id"] = account_id
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self._base}/pipeline", json=payload)
            resp.raise_for_status()
            return uuid.UUID(resp.json()["id"])

    async def count_active_runs(self, sample: int = 50) -> int:
        """How many runs are still in flight.

        This is the backpressure signal. Buffer's free queue holds 10 posts, so
        ingesting faster than we publish just converts fresh scripts into failed
        runs. Counting before submitting is what keeps the scout honest.
        """
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self._base}/pipeline", params={"limit": sample})
            resp.raise_for_status()
            runs = resp.json()
        return sum(1 for run in runs if run.get("status") in ACTIVE_STATUSES)

    async def count_active_runs_by_account(self, sample: int = 50) -> dict[str | None, int]:
        """Same backpressure signal as ``count_active_runs``, split per account.

        A separate call rather than a shared helper with ``count_active_runs``:
        the two are read by different callers (this one by the scout's
        per-account gate, the plain one by ``inbox.py``'s single global gate),
        and keeping them independent means a change to one can never silently
        change the other's already-tested behaviour.
        """
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self._base}/pipeline", params={"limit": sample})
            resp.raise_for_status()
            runs = resp.json()
        counts: dict[str | None, int] = {}
        for run in runs:
            if run.get("status") in ACTIVE_STATUSES:
                key = run.get("account_id")
                counts[key] = counts.get(key, 0) + 1
        return counts

    async def list_accounts(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self._base}/accounts")
            resp.raise_for_status()
            return resp.json()
