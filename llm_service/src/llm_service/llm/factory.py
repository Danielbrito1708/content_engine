from src.core import settings
from src.llm_service.llm.base import BaseLLMClient


def get_llm_client(model: str | None = None) -> BaseLLMClient:
    """Build the client for the configured provider.

    ``model`` overrides the default so a cheap model can handle mechanical work
    (moderation) while refinement keeps the good one — same provider, same key.
    """
    provider = settings.env.llm_provider
    model = model or settings.env.llm_model

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
