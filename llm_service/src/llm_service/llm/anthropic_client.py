import json
import os

import anthropic

from src.llm_service.llm.base import BaseLLMClient
from src.llm_service.schemas.refine import RefineResponse


class AnthropicClient(BaseLLMClient):
    def __init__(self, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._model = model

    async def complete(self, system: str, user: str) -> str:
        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text

    async def refine(self, system: str, user: str) -> RefineResponse:
        # Anthropic doesn't have json_object mode — append explicit instruction
        user_with_hint = user + "\n\nResponda APENAS com JSON válido, sem texto adicional."
        raw = await self.complete(system, user_with_hint)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(raw)
        return RefineResponse.model_validate(data)
