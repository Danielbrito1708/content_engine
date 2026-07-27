import json
from abc import ABC, abstractmethod

from src.llm_service.schemas.refine import RefineResponse


JSON_HINT = "\n\nResponda APENAS com JSON válido, sem texto adicional."


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    return raw


class BaseLLMClient(ABC):
    #: Providers with a native JSON mode ignore this; Anthropic has none, so the
    #: instruction has to ride along in the prompt.
    needs_json_hint: bool = False

    @abstractmethod
    async def complete(self, system: str, user: str) -> str:
        """Returns raw text response from the LLM."""

    async def complete_json(self, system: str, user: str) -> dict:
        """Completion parsed as JSON, tolerating fenced output."""
        if self.needs_json_hint:
            user = user + JSON_HINT
        return json.loads(_strip_fences(await self.complete(system, user)))

    async def refine(self, system: str, user: str) -> RefineResponse:
        raw = await self.complete(system, user)
        raw = _strip_fences(raw)
        data = json.loads(raw)
        return RefineResponse.model_validate(data)
