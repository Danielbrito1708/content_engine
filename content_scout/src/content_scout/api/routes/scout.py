from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.content_scout.db.engine import get_session
from src.content_scout.db.models import SeenItem, SeenStatus
from src.content_scout.schemas.scout import ScoutRunResponse, SeenItemResponse
from src.content_scout.scout import run_cycle

router = APIRouter(prefix="/scout")


@router.post("/run", response_model=ScoutRunResponse)
async def trigger_run():
    """Run one scouting cycle now and return what it did.

    Synchronous on purpose — a cycle is a handful of HTTP calls, and being able
    to see the counts immediately is the whole point when tuning filters.
    """
    report = await run_cycle()
    return ScoutRunResponse(**report.__dict__)


@router.get("/seen", response_model=list[SeenItemResponse])
async def list_seen(
    status: SeenStatus | None = None,
    story_tag: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """Audit trail. Filter by status to see what was rejected and why.

    ``story_tag`` is the calibration handle: listing ``weak_storytelling`` shows
    what the score is punishing, which is the only honest way to decide where
    ``min_story_score`` belongs.
    """
    stmt = select(SeenItem).order_by(SeenItem.created_at.desc()).limit(limit).offset(offset)
    if status is not None:
        stmt = stmt.where(SeenItem.status == status)
    if story_tag is not None:
        stmt = stmt.where(SeenItem.story_tag == story_tag)
    result = await session.execute(stmt)
    return result.scalars().all()
