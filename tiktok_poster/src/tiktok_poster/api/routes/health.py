from fastapi import APIRouter

from src.tiktok_poster.buffer.client import BufferClient

router = APIRouter()


@router.get("/health")
async def health():
    ok = await BufferClient().verify_connection()
    status = "ok" if ok else "degraded"
    return {"status": status, "checks": {"buffer": "ok" if ok else "unreachable"}}
