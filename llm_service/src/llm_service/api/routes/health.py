import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    provider = os.environ.get("LLM_PROVIDER", "openrouter")
    model = os.environ.get("LLM_MODEL", "anthropic/claude-3.5-sonnet")
    return {"status": "ok", "provider": provider, "model": model}
