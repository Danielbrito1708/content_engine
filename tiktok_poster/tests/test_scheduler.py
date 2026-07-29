from datetime import datetime, timedelta, timezone

from src.tiktok_poster.buffer.scheduler import continuation_slot, next_available_slot

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


# --- continuation_slot: partes de uma mesma história -------------------------


def test_continuation_lands_exactly_one_gap_after_the_previous_part():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    follows_at = now + timedelta(hours=5)

    slot = continuation_slot(follows_at, 30, [], queue_limit=10, now=now)

    assert slot == follows_at + timedelta(minutes=30)


def test_continuation_ignores_preferred_times():
    """O ponto da mudança: a parte 2 não espera o próximo 08:00 ou 20:00."""
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    follows_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    slot = continuation_slot(follows_at, 30, [], queue_limit=10, now=now)

    assert slot == datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)
    assert slot.hour not in (8, 20)


def test_continuation_never_schedules_into_the_past():
    """Run retomado muito depois: o Buffer recusaria um horário já vencido.

    O intervalo é espaçamento mínimo, não deslocamento fixo — a história volta
    a andar um intervalo a partir de agora.
    """
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    follows_at = now - timedelta(days=2)

    slot = continuation_slot(follows_at, 30, [], queue_limit=10, now=now)

    assert slot == now + timedelta(minutes=30)


def test_continuation_accepts_a_naive_previous_slot_as_utc():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    slot = continuation_slot(datetime(2026, 1, 1, 14, 0), 30, [], queue_limit=10, now=now)

    assert slot == datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)


def test_continuation_returns_none_when_queue_full():
    posts = [_make_post(1700000000 + i * 3600) for i in range(10)]
    follows_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)

    assert continuation_slot(follows_at, 30, posts, queue_limit=10) is None


def test_continuation_gap_is_configurable():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    follows_at = now + timedelta(hours=1)

    slot = continuation_slot(follows_at, 90, [], queue_limit=10, now=now)

    assert slot == follows_at + timedelta(minutes=90)
