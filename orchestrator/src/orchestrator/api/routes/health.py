from fastapi import APIRouter
from sqlalchemy import text

from src.orchestrator.db.engine import AsyncSessionLocal

router = APIRouter()


@router.get("/health")
async def health():
    checks = {}

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = str(exc)

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "checks": checks}
