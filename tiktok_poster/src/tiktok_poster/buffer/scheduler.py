from datetime import date, datetime, time, timedelta, timezone


def next_available_slot(
    pending_posts: list[dict],
    posts_per_day: int,
    preferred_times: list[str],
    queue_limit: int,
) -> datetime | None:
    """Returns the next available UTC datetime slot, or None if the queue is full.

    preferred_times: list of "HH:MM" strings in UTC, e.g. ["08:00", "20:00"].
    Scans forward day by day until a day with fewer than posts_per_day scheduled posts
    is found. Returns None if pending_posts has already reached queue_limit.
    """
    if len(pending_posts) >= queue_limit:
        return None

    slot_times = [_parse_time(t) for t in preferred_times]
    occupied: dict[date, set[time]] = {}

    for post in pending_posts:
        raw_ts = post.get("scheduled_at") or post.get("due_at") or post.get("created_at", 0)
        try:
            dt = datetime.fromtimestamp(int(raw_ts), tz=timezone.utc)
        except (TypeError, ValueError):
            continue
        day = dt.date()
        occupied.setdefault(day, set()).add(dt.time().replace(second=0, microsecond=0))

    now = datetime.now(tz=timezone.utc)
    check_day = now.date()

    for _ in range(60):
        taken = occupied.get(check_day, set())
        for slot_time in slot_times:
            candidate = datetime.combine(check_day, slot_time, tzinfo=timezone.utc)
            # occupied stores naive times (from dt.time()); compare without tzinfo
            slot_naive = slot_time.replace(second=0, microsecond=0, tzinfo=None)
            if candidate > now and slot_naive not in taken:
                return candidate
        check_day += timedelta(days=1)

    return None


def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m), tzinfo=timezone.utc)
