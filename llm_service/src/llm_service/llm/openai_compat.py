import os

from openai import AsyncOpenAI

from src.llm_service.llm.base import BaseLLMClient


class OpenAICompatClient(BaseLLMClient):
    """Works with OpenRouter and Chutes AI (both are OpenAI-compatible)."""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def complete(self, system: str, user: str) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        return resp.choices[0].message.content


def make_openrouter(model: str) -> OpenAICompatClient:
    return OpenAICompatClient(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        model=model,
    )


def make_chutes(model: str) -> OpenAICompatClient:
    return OpenAICompatClient(
        api_key=os.environ["CHUTES_API_KEY"],
        base_url=os.environ.get("CHUTES_BASE_URL", "https://llm.chutes.ai/v1"),
        model=model,
    )
