import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from structlog import get_logger

from src.core import settings
from src.tiktok_poster.buffer.client import BufferClient
from src.tiktok_poster.buffer.scheduler import continuation_slot, next_available_slot
from src.tiktok_poster.hashtags.selector import compose_caption, select_hashtags
from src.tiktok_poster.schemas.schedule import ScheduleRequest, ScheduleResponse
from src.tiktok_poster.storage.client import generate_presigned_url

router = APIRouter()
log = get_logger(__name__)


def _load_hashtag_config() -> dict:
    path = Path(settings.ROOT_DIR) / "hashtags.json"
    with open(path) as f:
        return json.load(f)


@router.post("/schedule", response_model=ScheduleResponse, status_code=201)
async def schedule(body: ScheduleRequest) -> ScheduleResponse:
    log.info("schedule request", series_id=body.series_id, part=body.part_number)

    cfg = settings.CONFIG
    posting_cfg = cfg.posting
    posts_per_day: int = posting_cfg.posts_per_day
    preferred_times: list[str] = [t.strip() for t in posting_cfg.preferred_times.split(",")]
    queue_limit: int = posting_cfg.buffer_queue_limit
    ttl: int = posting_cfg.presigned_url_ttl
    gap_minutes: int = posting_cfg.series_gap_minutes

    hashtag_cfg = cfg.hashtags
    mandatory: list[str] = [t.strip() for t in hashtag_cfg.mandatory.split(",")]
    max_total: int = hashtag_cfg.max_total

    buffer = BufferClient()

    pending = await buffer.get_pending_posts()
    if body.follows_at is not None:
        slot = continuation_slot(body.follows_at, gap_minutes, pending, queue_limit)
    else:
        slot = next_available_slot(pending, posts_per_day, preferred_times, queue_limit)
    if slot is None:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "buffer_queue_full",
                "message": f"Buffer queue has reached the {queue_limit}-post limit. Retry later.",
                "pending_count": len(pending),
            },
        )

    hashtag_data = _load_hashtag_config()
    hints: list[str] = body.classification.get("hashtag_hints", [])
    hashtags = select_hashtags(hints, mandatory, hashtag_data.get("pool", []), max_total)

    cta_list: list[str] = body.classification.get("cta_per_part", [])
    cta = cta_list[body.part_number - 1] if body.part_number <= len(cta_list) else ""
    caption = compose_caption(cta, hashtags, body.part_number, body.total_parts)

    bucket = cfg.storage.bucket
    video_url = await generate_presigned_url(bucket, body.video_key, ttl)

    data = await buffer.create_post(video_url, caption, slot)

    update_id: str = str(data.get("updates", [{}])[0].get("id", ""))
    log.info(
        "post scheduled",
        series_id=body.series_id,
        part=body.part_number,
        of=body.total_parts,
        slot=slot.isoformat(),
        continuation=body.follows_at is not None,
    )

    return ScheduleResponse(scheduled_at=slot, buffer_update_id=update_id)
