from dataclasses import dataclass

import httpx

from src.core import settings


@dataclass
class RefineResult:
    parts: list[str]
    classification: dict


class LLMClient:
    def __init__(self):
        self._base = settings.CONFIG.services.llm_url

    async def refine(self, script: str, metadata: dict) -> RefineResult:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{self._base}/refine", json={"script": script, "metadata": metadata})
            resp.raise_for_status()
            data = resp.json()
        return RefineResult(parts=data["parts"], classification=data["classification"])
