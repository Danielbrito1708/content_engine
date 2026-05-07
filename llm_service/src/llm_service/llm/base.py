import json
from abc import ABC, abstractmethod

from src.llm_service.schemas.refine import RefineResponse


class BaseLLMClient(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str) -> str:
        """Returns raw text response from the LLM."""

    async def refine(self, system: str, user: str) -> RefineResponse:
        raw = await self.complete(system, user)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(raw)
        return RefineResponse.model_validate(data)
