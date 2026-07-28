from dataclasses import dataclass

import httpx

from src.core import settings


@dataclass
class RefineResult:
    parts: list[str]
    classification: dict
    #: Frase gancho do roteiro. Vazia quando o `llm_service` do outro lado
    #: ainda não devolve o campo — deploy dos dois serviços não é atômico.
    hook: str = ""


class LLMClient:
    def __init__(self):
        self._base = settings.CONFIG.services.llm_url

    async def refine(self, script: str, metadata: dict) -> RefineResult:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{self._base}/refine", json={"script": script, "metadata": metadata})
            resp.raise_for_status()
            data = resp.json()
        return RefineResult(
            parts=data["parts"],
            classification=data["classification"],
            hook=(data.get("hook") or "").strip(),
        )
