import os

from src.llm_service.llm.base import BaseLLMClient


def get_llm_client() -> BaseLLMClient:
    provider = os.environ.get("LLM_PROVIDER", "openrouter").lower()
    model = os.environ.get("LLM_MODEL", "anthropic/claude-3.5-sonnet")

    if provider == "openrouter":
        from src.llm_service.llm.openai_compat import make_openrouter
        return make_openrouter(model)

    if provider == "chutes":
        from src.llm_service.llm.openai_compat import make_chutes
        return make_chutes(model)

    if provider == "anthropic":
        from src.llm_service.llm.anthropic_client import AnthropicClient
        return AnthropicClient(model=model)

    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}. Use 'openrouter', 'chutes' or 'anthropic'.")
