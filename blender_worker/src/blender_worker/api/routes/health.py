from fastapi import APIRouter
from sqlalchemy import text

from src.blender_worker.db.engine import engine
from src.blender_worker.storage.client import get_s3_client

router = APIRouter()


@router.get("/health")
async def health():
    checks: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"

    try:
        get_s3_client().list_buckets()
        checks["minio"] = "ok"
    except Exception as exc:
        checks["minio"] = f"error: {exc}"

    ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if ok else "degraded", "checks": checks}
