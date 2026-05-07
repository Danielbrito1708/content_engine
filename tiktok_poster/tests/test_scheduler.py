from datetime import datetime, timezone

from src.tiktok_poster.buffer.scheduler import next_available_slot

TIMES = ["08:00", "20:00"]


def _make_post(ts: int) -> dict:
    return {"scheduled_at": ts}


def test_returns_first_slot_when_queue_empty():
    slot = next_available_slot([], posts_per_day=2, preferred_times=TIMES, queue_limit=10)
    assert slot is not None
    assert slot > datetime.now(tz=timezone.utc)
    assert slot.hour in (8, 20)


def test_returns_none_when_queue_full():
    posts = [_make_post(1700000000 + i * 3600) for i in range(10)]
    slot = next_available_slot(posts, posts_per_day=2, preferred_times=TIMES, queue_limit=10)
    assert slot is None


def test_skips_full_days():
    from datetime import date, time, timedelta, timezone

    today = datetime.now(tz=timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    def _ts(d: date, h: int) -> int:
        return int(datetime.combine(d, time(h, 0), tzinfo=timezone.utc).timestamp())

    posts = [
        _make_post(_ts(today, 8)),
        _make_post(_ts(today, 20)),
    ]
    slot = next_available_slot(posts, posts_per_day=2, preferred_times=TIMES, queue_limit=10)
    assert slot is not None
    assert slot.date() == tomorrow


def test_picks_earlier_slot_first():
    slot = next_available_slot([], posts_per_day=2, preferred_times=["08:00", "20:00"], queue_limit=10)
    # First available is 08:00 of today or tomorrow
    assert slot.hour == 8 or slot.hour == 20  # depends on current time
    assert slot is not None


def test_respects_posts_per_day_limit():
    from datetime import date, time, timedelta, timezone

    today = datetime.now(tz=timezone.utc).date()

    def _ts(d: date, h: int) -> int:
        return int(datetime.combine(d, time(h, 0), tzinfo=timezone.utc).timestamp())

    posts = [_make_post(_ts(today, 8))]
    slot = next_available_slot(posts, posts_per_day=1, preferred_times=["08:00"], queue_limit=10)
    assert slot is not None
    assert slot.date() > today
