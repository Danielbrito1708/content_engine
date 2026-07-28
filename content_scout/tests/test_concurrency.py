"""Two cycles at once — the failure that produced duplicate stories.

Measured live: a manual ``POST /scout/run`` right after a deploy ran head to
head with the cycle the periodic loop fires on startup. The Reddit spacing
halved (34s apart, both taking 429s), both cycles passed the same dedup read,
and the second commit died on the unique index — rolling back audit rows for
stories whose pipeline runs had already been created.
"""

import asyncio

import pytest
from sqlalchemy import select

from src.content_scout.clients.orchestrator import OrchestratorClient
from src.content_scout.db.models import SeenItem, SeenStatus
from src.content_scout.scout import _record, _cycle_lock, run_cycle
from src.content_scout.sources.base import Candidate
from src.content_scout.sources.reddit import RedditSource, shared_throttle


def _candidate(external_id: str) -> Candidate:
    return Candidate(
        source="reddit",
        external_id=external_id,
        origin="r/desabafos",
        title="Título",
        text="a" * 1000,
        url=f"https://reddit.com/{external_id}",
    )


def _seen_row(external_id: str) -> SeenItem:
    return SeenItem(
        source="reddit",
        external_id=external_id,
        origin="r/desabafos",
        title="Título",
        url="https://reddit.com/x",
        char_count=1000,
        status=SeenStatus.submitted,
    )


# ── shared throttle ────────────────────────────────────────────────────────

@pytest.mark.no_db
def test_every_source_instance_shares_one_throttle():
    """``build_sources()`` mints a new RedditSource per cycle, so a per-instance
    throttle spaces out requests within a cycle and nothing else."""
    first = RedditSource(
        base_url="https://reddit.com", subreddits=["a"], time_filter="week",
        limit_per_subreddit=5, user_agent="t", timeout=1, request_delay=0, comments_limit=0,
    )
    second = RedditSource(
        base_url="https://reddit.com", subreddits=["b"], time_filter="week",
        limit_per_subreddit=5, user_agent="t", timeout=1, request_delay=0, comments_limit=0,
    )

    assert first._throttle is second._throttle
    assert first._throttle is shared_throttle()


@pytest.mark.no_db
def test_the_last_configured_delay_wins():
    """Which keeps the suite at 0 instead of inheriting production's 60s."""
    shared_throttle(60)
    RedditSource(
        base_url="https://reddit.com", subreddits=["a"], time_filter="week",
        limit_per_subreddit=5, user_agent="t", timeout=1, request_delay=0, comments_limit=0,
    )
    assert shared_throttle().delay == 0


@pytest.mark.no_db
async def test_the_throttle_actually_spaces_two_instances_apart():
    shared_throttle(0.05)
    try:
        a = shared_throttle()
        loop = asyncio.get_running_loop()
        await a.wait()
        start = loop.time()
        await a.wait()
        assert loop.time() - start >= 0.04
    finally:
        shared_throttle(0)


# ── one cycle at a time ────────────────────────────────────────────────────

class _SilentSource:
    name = "reddit"

    async def fetch(self):
        return []


async def test_a_second_cycle_declines_instead_of_racing():
    async with _cycle_lock:
        report = await run_cycle(sources=[_SilentSource()])

    assert report.already_running is True
    assert report.fetched == 0


async def test_the_lock_is_released_after_a_cycle(monkeypatch):
    async def no_active_runs(_self):
        return 0

    monkeypatch.setattr(OrchestratorClient, "count_active_runs", no_active_runs)

    await run_cycle(sources=[_SilentSource()])

    assert not _cycle_lock.locked()


# ── audit rows survive a conflict ──────────────────────────────────────────

async def test_a_duplicate_row_is_tolerated_not_fatal(session):
    """The unique index used to take down the whole cycle, rolling back rows for
    runs that already existed in the orchestrator."""
    assert await _record(session, _seen_row("t3_dup")) is True
    assert await _record(session, _seen_row("t3_dup")) is False

    result = await session.execute(
        select(SeenItem).where(SeenItem.external_id == "t3_dup")
    )
    assert len(result.scalars().all()) == 1


async def test_a_conflict_does_not_erase_earlier_rows(session):
    """This is the part that mattered: the batch commit meant one conflict
    discarded every row of the cycle, including submitted stories."""
    await _record(session, _seen_row("t3_first"))
    await _record(session, _seen_row("t3_dup"))
    await _record(session, _seen_row("t3_dup"))

    result = await session.execute(select(SeenItem.external_id))
    ids = set(result.scalars().all())
    assert {"t3_first", "t3_dup"} <= ids
