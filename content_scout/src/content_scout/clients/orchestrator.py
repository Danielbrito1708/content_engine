import uuid

import httpx

from src.core import settings

# A run in any of these states still owes the Buffer queue a slot. Anything else
# has either landed or died, and no longer counts against the backlog.
ACTIVE_STATUSES = {"pending", "refining", "refined", "processing", "scheduling"}


class OrchestratorClient:
    def __init__(self):
        self._base = settings.CONFIG.services.orchestrator_url

    async def create_pipeline(self, script: str, metadata: dict) -> uuid.UUID:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/pipeline",
                json={"script": script, "metadata": metadata},
            )
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
