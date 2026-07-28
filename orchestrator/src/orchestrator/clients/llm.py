from dataclasses import dataclass

from src.core import settings
from src.orchestrator.clients.http import request


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
        resp = await request(
            "POST",
            f"{self._base}/refine",
            timeout=120,
            json={"script": script, "metadata": metadata},
        )
        data = resp.json()
        return RefineResult(
            parts=data["parts"],
            classification=data["classification"],
            hook=(data.get("hook") or "").strip(),
        )
