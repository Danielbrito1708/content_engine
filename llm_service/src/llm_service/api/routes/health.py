from fastapi import APIRouter

from src.core import settings

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "provider": settings.env.llm_provider, "model": settings.env.llm_model}
