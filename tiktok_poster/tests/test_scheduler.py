from datetime import date, datetime, timedelta, timezone

from src.tiktok_poster.buffer.scheduler import (
    _parse_time,
    _slots_for_day,
    continuation_slot,
    next_available_slot,
    parse_warmup_steps,
    warmup_cap_for_day,
)

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


# --- rampa de aquecimento: funções puras ------------------------------------


def test_parse_warmup_steps():
    assert parse_warmup_steps("1x7,2x7,3") == [(1, 7), (2, 7), (3, None)]


def test_parse_warmup_steps_tolerates_spaces_and_empty_entries():
    assert parse_warmup_steps(" 1x7 , , 2 ") == [(1, 7), (2, None)]


def test_warmup_cap_for_day_first_step():
    started = date(2026, 9, 1)
    assert warmup_cap_for_day(started, "1x7,2x7,3", date(2026, 9, 1), default_cap=3) == 1
    assert warmup_cap_for_day(started, "1x7,2x7,3", date(2026, 9, 7), default_cap=3) == 1


def test_warmup_cap_for_day_second_step():
    started = date(2026, 9, 1)
    assert warmup_cap_for_day(started, "1x7,2x7,3", date(2026, 9, 8), default_cap=3) == 2
    assert warmup_cap_for_day(started, "1x7,2x7,3", date(2026, 9, 14), default_cap=3) == 2


def test_warmup_cap_for_day_permanent_step_never_ends():
    started = date(2026, 9, 1)
    assert warmup_cap_for_day(started, "1x7,2x7,3", date(2026, 9, 15), default_cap=99) == 3
    assert warmup_cap_for_day(started, "1x7,2x7,3", date(2028, 1, 1), default_cap=99) == 3


def test_warmup_cap_for_day_falls_back_to_default_without_permanent_step():
    """Todos os degraus com dias finitos, esgotados: config incompleta não
    pode travar o agendamento — cai no teto de regime cheio."""
    started = date(2026, 9, 1)
    assert warmup_cap_for_day(started, "1x7,2x7", date(2026, 9, 20), default_cap=5) == 5


def test_warmup_cap_for_day_empty_steps_falls_back_to_default():
    assert warmup_cap_for_day(date(2026, 9, 1), "", date(2026, 9, 1), default_cap=3) == 3


def test_warmup_cap_for_day_before_start_clamps_to_day_zero():
    """`started_on` no futuro (relógio dessincronizado, ou dado incorreto) não
    pode gerar `elapsed` negativo — trata como o primeiro dia da rampa."""
    started = date(2026, 9, 10)
    assert warmup_cap_for_day(started, "1x7,2x7,3", date(2026, 9, 1), default_cap=3) == 1


# --- rampa aplicada em next_available_slot -----------------------------------


def test_next_available_slot_accepts_a_plain_int_unchanged():
    """Retrocompatibilidade: todo chamador antigo passa `int`, e o resultado
    não pode mudar com a introdução do `Callable`."""
    slot = next_available_slot([], posts_per_day=2, preferred_times=TIMES, queue_limit=10)
    assert slot is not None
    assert slot.hour in (8, 20)


def test_next_available_slot_reduced_cap_via_callable_limits_the_day():
    from datetime import time as time_cls

    today = datetime.now(tz=timezone.utc).date()

    def _ts(d: date, h: int) -> int:
        return int(datetime.combine(d, time_cls(h, 0), tzinfo=timezone.utc).timestamp())

    # Um post já sai hoje às 08:00 — com teto reduzido a 1/dia, o próximo
    # slot livre não pode ser 20:00 de hoje (isso exigiria cap >= 2).
    posts = [_make_post(_ts(today, 8))]
    slot = next_available_slot(posts, posts_per_day=lambda _day: 1, preferred_times=TIMES, queue_limit=10)
    assert slot is not None
    assert slot.date() > today


def test_slots_for_day_rotates_across_days():
    """Mesmo teto (1) em dois dias sucessivos não pode escolher sempre o
    mesmo horário — senão vira a assinatura fixa que hashtags já corrigiram."""
    slot_times = [_parse_time(t) for t in ["08:00", "14:00", "20:00"]]
    day_a = date(2026, 9, 1)
    day_b = day_a + timedelta(days=1)

    assert _slots_for_day(slot_times, cap=1, rotation_index=day_a.toordinal()) != _slots_for_day(
        slot_times, cap=1, rotation_index=day_b.toordinal()
    )


def test_slots_for_day_full_cap_returns_everything_unrotated():
    """Sem redução de teto (o caso de sempre), a lista sai inteira e na
    ordem original — nenhuma rotação é acionada."""
    slot_times = [_parse_time(t) for t in TIMES]
    assert _slots_for_day(slot_times, cap=2, rotation_index=999) == slot_times
