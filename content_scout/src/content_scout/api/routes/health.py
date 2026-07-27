from fastapi import APIRouter
from sqlalchemy import text

from src.content_scout.db.engine import AsyncSessionLocal
from src.core import settings

router = APIRouter()


@router.get("/health")
async def health():
    checks = {"db": "ok"}
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — health must report, not raise
        checks["db"] = f"error: {exc}"

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "scout_enabled": settings.env.scout_enabled, "checks": checks}
