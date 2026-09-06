from datetime import date, datetime, time, timedelta, timezone
from typing import Callable


def next_available_slot(
    pending_posts: list[dict],
    posts_per_day: "int | Callable[[date], int]",
    preferred_times: list[str],
    queue_limit: int,
) -> datetime | None:
    """Returns the next available UTC datetime slot, or None if the queue is full.

    preferred_times: list of "HH:MM" strings in UTC, e.g. ["08:00", "20:00"].
    Scans forward day by day until a day with fewer than posts_per_day scheduled posts
    is found. Returns None if pending_posts has already reached queue_limit.

    ``posts_per_day`` pode ser um `int` fixo (comportamento de sempre) ou um
    `Callable[[date], int]` — a rampa de aquecimento (`warmup_cap_for_day`)
    precisa disto porque o teto muda de valor **durante** a própria varredura:
    um `int` calculado uma vez antes de chamar esta função acertaria só o
    primeiro dia examinado, e a varredura anda vários dias à frente quando a
    fila do dia já está cheia. Ver docs/vision.md → "Rampa de publicação".

    Quando o teto do dia é menor que `len(preferred_times)`, os horários
    candidatos daquele dia são um **subconjunto rotacionado** (por
    `check_day.toordinal()`), não sempre os primeiros da lista — senão a conta
    publicaria sempre no mesmo horário todo dia de rampa, a mesma assinatura
    de conta automatizada já corrigida para hashtags (28/08/2026).
    """
    if len(pending_posts) >= queue_limit:
        return None

    slot_times = [_parse_time(t) for t in preferred_times]
    cap_for_day: Callable[[date], int] = (
        posts_per_day if callable(posts_per_day) else (lambda _day: posts_per_day)
    )
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
        cap = cap_for_day(check_day)
        todays_slot_times = _slots_for_day(slot_times, cap, check_day.toordinal())
        taken = occupied.get(check_day, set())
        for slot_time in todays_slot_times:
            candidate = datetime.combine(check_day, slot_time, tzinfo=timezone.utc)
            # occupied stores naive times (from dt.time()); compare without tzinfo
            slot_naive = slot_time.replace(second=0, microsecond=0, tzinfo=None)
            if candidate > now and slot_naive not in taken:
                return candidate
        check_day += timedelta(days=1)

    return None


def _slots_for_day(slot_times: list[time], cap: int, rotation_index: int) -> list[time]:
    """Subconjunto de `slot_times` disponível num dia com teto `cap`.

    `cap >= len(slot_times)` é o caso de sempre (sem rampa, ou rampa já no
    regime cheio): devolve a lista inteira, sem rotação — mesmo resultado de
    antes desta função existir. Só quando o teto reduz é que a rotação entra,
    e ela desliza pela lista por `rotation_index` (o ordinal do dia) para o
    horário escolhido variar dia a dia em vez de fixar sempre no primeiro.
    """
    if cap >= len(slot_times):
        return slot_times
    if cap <= 0:
        return []
    n = len(slot_times)
    start = rotation_index % n
    rotated = slot_times[start:] + slot_times[:start]
    return rotated[:cap]


def parse_warmup_steps(steps: str) -> list[tuple[int, "int | None"]]:
    """`"1x7,2x7,3"` -> `[(1, 7), (2, 7), (3, None)]`.

    Cada degrau é `<teto>x<dias>`; o último pode vir sem `x<dias>` — é o
    regime permanente, o que a rampa converge para e nunca deixa de valer.
    Espaços em volta das vírgulas são tolerados; entradas vazias, ignoradas.
    """
    steps_list: list[tuple[int, int | None]] = []
    for raw in steps.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if "x" in raw:
            cap_s, days_s = raw.split("x", 1)
            steps_list.append((int(cap_s), int(days_s)))
        else:
            steps_list.append((int(raw), None))
    return steps_list


def warmup_cap_for_day(started_on: date, steps: str, day: date, default_cap: int) -> int:
    """Teto de posts/dia (por história) para `day`, dado que a rampa começou em
    `started_on` com os degraus de `steps`.

    `default_cap` é o teto de regime cheio (`[posting] posts_per_day`) — usado
    se `steps` vier vazio ou se todos os degraus tiverem `dias` finitos e se
    esgotarem sem um degrau permanente no fim (configuração incompleta, não
    motivo para travar o agendamento).
    """
    elapsed = max((day - started_on).days, 0)
    parsed = parse_warmup_steps(steps)
    if not parsed:
        return default_cap

    cursor = 0
    for cap, days in parsed:
        if days is None:
            return cap
        if elapsed < cursor + days:
            return cap
        cursor += days
    return default_cap


def continuation_slot(
    follows_at: datetime,
    gap_minutes: int,
    pending_posts: list[dict],
    queue_limit: int,
    now: datetime | None = None,
) -> datetime | None:
    """Slot for a part that continues a story already booked at ``follows_at``.

    A story split in two is not two posts — it is one story continued, so the
    second half hangs off the first instead of taking the next slot on the
    calendar. ``preferred_times`` and ``posts_per_day`` are deliberately not
    consulted: they pace independent stories, and letting them pace a
    continuation would drop the rest of the story hours (or a day) later.

    ``queue_limit`` still applies — it is Buffer's own ceiling, not a rhythm
    choice, and going past it would fail at the API instead of here.
    """
    if len(pending_posts) >= queue_limit:
        return None

    now = now or datetime.now(tz=timezone.utc)
    gap = timedelta(minutes=gap_minutes)

    if follows_at.tzinfo is None:
        follows_at = follows_at.replace(tzinfo=timezone.utc)

    # The gap is a *minimum* spacing, not a fixed offset. When the previous part
    # is already in the past — a run resumed long after a restart — anchoring on
    # it would ask Buffer to schedule backwards; the story just resumes a gap
    # from now instead.
    return max(follows_at + gap, now + gap)


def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m), tzinfo=timezone.utc)


#: Fuso de Brasília como offset fixo — mesma convenção do resto do serviço
#: (ver `preferred_times` acima e `docs/vsel.md`/`servidor.md` na raiz): sem
#: horário de verão desde 2019, então UTC-3 constante é exato, não aproximado.
_BRT = timezone(timedelta(hours=-3))


def timing_bucket(
    scheduled_at: datetime,
    preferred_times: list[str],
    tolerance_minutes: int = 15,
) -> str:
    """Rotula ``scheduled_at`` pelo slot de `[posting] preferred_times` mais
    próximo, em horário de Brasília — para o teste A/B por horário de postagem.

    ``preferred_times`` são as mesmas strings ``"HH:MM"`` em UTC que
    `next_available_slot` já lê do `config.ini`. Convertidas para BRT e
    comparadas contra o minuto-do-dia de ``scheduled_at`` (também convertido),
    o slot preferido mais próximo dentro de ``tolerance_minutes`` vira o rótulo
    (``"11h"``, ``"15h"``, ``"19h"`` na grade atual); fora da tolerância — o
    caso de `continuation_slot`, que pendura fora da grade de propósito —
    devolve ``"other"``.

    ``tolerance_minutes`` (15) é **menor que `series_gap_minutes`** (30) de
    propósito: `next_available_slot` sempre devolve o horário exato de
    `preferred_times` (sem jitter), então qualquer tolerância menor que o
    intervalo de continuação já basta para não confundir "pendurado 30 min
    depois do slot" com "é o slot". Uma folga de 15 min sobra para eventual
    imprecisão de ponto flutuante, sem chegar perto dos 30 min da continuação.

    Pura: recebe `preferred_times` já lidos do config, em vez de ler
    `settings` diretamente, para que `api/routes/schedule.py` reuse a mesma
    lista que já usa para achar o slot, e para que o teste não precise montar
    um `config.ini` só para isto.
    """
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    brt = scheduled_at.astimezone(_BRT)
    target_minutes = brt.hour * 60 + brt.minute

    best_label = "other"
    best_diff = tolerance_minutes + 1
    for raw in preferred_times:
        hh, mm = raw.split(":")
        utc_minutes = int(hh) * 60 + int(mm)
        # Mesmo offset fixo aplicado ao minuto-do-dia, sem passar por um
        # datetime completo — o resultado só depende da hora, não da data.
        brt_minutes = (utc_minutes - 180) % 1440
        diff = abs(target_minutes - brt_minutes)
        diff = min(diff, 1440 - diff)
        if diff <= tolerance_minutes and diff < best_diff:
            best_diff = diff
            best_label = f"{brt_minutes // 60}h"

    return best_label
