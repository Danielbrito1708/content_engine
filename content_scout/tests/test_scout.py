import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from src.content_scout.clients.llm import ModerationClient, ModerationError, Verdict
from src.content_scout.clients.orchestrator import OrchestratorClient
from src.content_scout.db.models import SeenItem, SeenStatus
from src.content_scout.scout import interleave_by_origin, run_cycle
from src.content_scout.sources.base import Candidate
from src.core import settings


class FakeSource:
    name = "reddit"

    def __init__(self, candidates):
        self._candidates = candidates

    async def fetch(self):
        return list(self._candidates)


def _candidate(external_id: str, chars: int = 1000, title: str = "Título",
               origin: str = "r/desabafos") -> Candidate:
    return Candidate(
        source="reddit",
        external_id=external_id,
        origin=origin,
        title=title,
        text="a" * chars,
        url=f"https://reddit.com/{external_id}",
    )


def test_interleave_alternates_between_origins():
    ordered = interleave_by_origin([
        _candidate("a1", origin="r/a"), _candidate("a2", origin="r/a"),
        _candidate("b1", origin="r/b"), _candidate("b2", origin="r/b"),
    ])
    assert [c.external_id for c in ordered] == ["a1", "b1", "a2", "b2"]


def test_interleave_preserves_ranking_within_each_origin():
    """Position in the feed is the only quality signal — it must survive."""
    ordered = interleave_by_origin([
        _candidate(f"a{i}", origin="r/a") for i in range(3)
    ] + [_candidate("b0", origin="r/b")])
    per_origin = [c.external_id for c in ordered if c.origin == "r/a"]
    assert per_origin == ["a0", "a1", "a2"]


def test_interleave_handles_uneven_groups():
    ordered = interleave_by_origin([
        _candidate("a1", origin="r/a"), _candidate("a2", origin="r/a"),
        _candidate("a3", origin="r/a"), _candidate("b1", origin="r/b"),
    ])
    assert [c.external_id for c in ordered] == ["a1", "b1", "a2", "a3"]


def test_interleave_single_origin_is_unchanged():
    ids = ["a1", "a2", "a3"]
    ordered = interleave_by_origin([_candidate(i, origin="r/a") for i in ids])
    assert [c.external_id for c in ordered] == ids


def test_interleave_empty():
    assert interleave_by_origin([]) == []


@pytest.fixture
def submissions(monkeypatch):
    """Capture what the scout sends to the orchestrator, with no network."""
    sent = []

    async def fake_create(self, script, metadata):
        run_id = uuid.uuid4()
        sent.append({"script": script, "metadata": metadata, "run_id": run_id})
        return run_id

    monkeypatch.setattr(OrchestratorClient, "create_pipeline", fake_create)
    return sent


@pytest.fixture
def set_active_runs(monkeypatch):
    def _set(count):
        async def fake_count(self, sample=50):
            return count
        monkeypatch.setattr(OrchestratorClient, "count_active_runs", fake_count)
    _set(0)
    return _set


@pytest.fixture(autouse=True)
def moderation(monkeypatch):
    """Every candidate is safe unless a test says otherwise. No LLM is called."""
    calls: list[tuple[str, str]] = []
    behavior = {"fn": lambda title, text: Verdict(safe=True)}

    async def fake_check(self, title, text):
        calls.append((title, text))
        result = behavior["fn"](title, text)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ModerationClient, "check", fake_check)
    return SimpleNamespace(calls=calls, set=lambda fn: behavior.__setitem__("fn", fn))


@pytest.fixture
def budget(monkeypatch):
    def _set(max_pending_runs=5, max_per_cycle=2):
        monkeypatch.setattr(settings.CONFIG.scout, "max_pending_runs", max_pending_runs)
        monkeypatch.setattr(settings.CONFIG.scout, "max_per_cycle", max_per_cycle)
    _set()
    return _set


async def _seen_rows(session):
    result = await session.execute(select(SeenItem).order_by(SeenItem.external_id))
    return result.scalars().all()


async def test_submits_fresh_candidate_and_records_it(submissions, set_active_runs, budget, session):
    source = FakeSource([_candidate("t3_a")])

    report = await run_cycle([source])

    assert report.fetched == 1
    assert report.submitted == 1
    assert report.submitted_ids == ["t3_a"]
    assert len(submissions) == 1

    rows = await _seen_rows(session)
    assert len(rows) == 1
    assert rows[0].status == SeenStatus.submitted
    assert rows[0].pipeline_run_id == submissions[0]["run_id"]


async def test_metadata_carries_provenance_to_orchestrator(submissions, set_active_runs, budget):
    await run_cycle([FakeSource([_candidate("t3_a")])])

    meta = submissions[0]["metadata"]
    assert meta["source"] == "reddit"
    assert meta["origin"] == "r/desabafos"
    assert meta["url"] == "https://reddit.com/t3_a"


async def test_already_seen_candidate_is_not_resubmitted(submissions, set_active_runs, budget):
    candidate = _candidate("t3_a")

    first = await run_cycle([FakeSource([candidate])])
    second = await run_cycle([FakeSource([candidate])])

    assert first.submitted == 1
    assert second.submitted == 0
    assert second.already_seen == 1
    assert len(submissions) == 1


async def test_duplicate_within_one_cycle_is_submitted_once(submissions, set_active_runs, budget):
    """The same post can top two feeds at once; the unique index must not blow up."""
    candidate = _candidate("t3_a")

    report = await run_cycle([FakeSource([candidate]), FakeSource([candidate])])

    assert report.fetched == 2
    assert report.submitted == 1
    assert len(submissions) == 1


async def test_filtered_candidate_is_recorded_but_not_submitted(
    submissions, set_active_runs, budget, session
):
    report = await run_cycle([FakeSource([_candidate("t3_short", chars=10)])])

    assert report.filtered == 1
    assert report.submitted == 0
    assert submissions == []

    rows = await _seen_rows(session)
    assert rows[0].status == SeenStatus.filtered
    assert rows[0].skip_reason == "too_short:10"


async def test_filtered_candidate_is_not_reconsidered_next_cycle(
    submissions, set_active_runs, budget
):
    """Recording rejects is what stops the scout re-fetching the same junk forever."""
    candidate = _candidate("t3_short", chars=10)

    await run_cycle([FakeSource([candidate])])
    second = await run_cycle([FakeSource([candidate])])

    assert second.already_seen == 1
    assert second.filtered == 0


async def test_backpressure_blocks_submission_when_queue_is_busy(
    submissions, set_active_runs, budget, session
):
    """Ingesting past the Buffer queue just turns fresh scripts into failed runs."""
    budget(max_pending_runs=5)
    set_active_runs(5)

    report = await run_cycle([FakeSource([_candidate("t3_a")])])

    assert report.skipped_no_capacity is True
    assert report.submitted == 0
    assert report.active_runs == 5
    assert submissions == []
    assert await _seen_rows(session) == []


async def test_no_capacity_still_records_filtered_items(
    submissions, set_active_runs, budget, session
):
    """A full queue costs nothing: the junk is still burned down for next time."""
    budget(max_pending_runs=5)
    set_active_runs(5)

    report = await run_cycle([FakeSource([_candidate("t3_short", chars=10)])])

    assert report.skipped_no_capacity is True
    assert report.filtered == 1
    rows = await _seen_rows(session)
    assert [r.status for r in rows] == [SeenStatus.filtered]


async def test_max_per_cycle_caps_submissions(submissions, set_active_runs, budget):
    budget(max_pending_runs=99, max_per_cycle=2)

    report = await run_cycle([FakeSource([_candidate(f"t3_{i}") for i in range(5)])])

    assert report.submitted == 2
    assert len(submissions) == 2


async def test_first_origin_does_not_monopolize_the_budget(
    submissions, set_active_runs, budget
):
    """Regression: the richest subreddit used to take every slot.

    Observed live before the fix — both submissions came from the first sub
    while the second contributed five candidates and won nothing.
    """
    budget(max_pending_runs=99, max_per_cycle=2)
    rich = [_candidate(f"a{i}", origin="r/desabafos") for i in range(13)]
    poor = [_candidate(f"b{i}", origin="r/relacionamentos") for i in range(5)]

    report = await run_cycle([FakeSource(rich + poor)])

    assert report.submitted == 2
    origins = {m["metadata"]["origin"] for m in submissions}
    assert origins == {"r/desabafos", "r/relacionamentos"}


async def test_rotation_reaches_every_origin_across_cycles(
    submissions, set_active_runs, budget
):
    """With 3 origins and a budget of 2, the third must not starve.

    Plain interleaving truncated at the budget yields ``a0, b0`` forever — the
    third origin only gets a turn once the first two run dry. Ordering the
    groups by how many each origin has already had published fixes it.
    """
    budget(max_pending_runs=99, max_per_cycle=2)

    def batch():
        return FakeSource(
            [_candidate(f"a{i}", origin="r/a") for i in range(3)]
            + [_candidate(f"b{i}", origin="r/b") for i in range(3)]
            + [_candidate(f"c{i}", origin="r/c") for i in range(3)]
        )

    for _ in range(3):
        await run_cycle([batch()])

    picked = [m["metadata"]["url"].rsplit("/", 1)[-1] for m in submissions]
    assert picked == ["a0", "b0", "c0", "a1", "b1", "c1"]


def test_interleave_puts_least_served_origin_first():
    ordered = interleave_by_origin(
        [_candidate("a1", origin="r/a"), _candidate("b1", origin="r/b")],
        usage={"r/a": 5, "r/b": 0},
    )
    assert [c.origin for c in ordered] == ["r/b", "r/a"]


def test_interleave_without_usage_is_deterministic_by_name():
    ordered = interleave_by_origin([
        _candidate("z1", origin="r/z"), _candidate("a1", origin="r/a"),
    ])
    assert [c.origin for c in ordered] == ["r/a", "r/z"]


async def test_remaining_capacity_caps_submissions_below_per_cycle(
    submissions, set_active_runs, budget
):
    """With 4 of 5 slots taken, only one more may go in even though the cap is 2."""
    budget(max_pending_runs=5, max_per_cycle=2)
    set_active_runs(4)

    report = await run_cycle([FakeSource([_candidate("t3_a"), _candidate("t3_b")])])

    assert report.submitted == 1


async def test_failed_submission_is_recorded_and_cycle_continues(
    monkeypatch, set_active_runs, budget, session
):
    budget(max_pending_runs=99, max_per_cycle=5)
    calls = []

    async def flaky_create(self, script, metadata):
        calls.append(metadata["url"])
        if metadata["url"].endswith("t3_bad"):
            raise RuntimeError("orchestrator down")
        return uuid.uuid4()

    monkeypatch.setattr(OrchestratorClient, "create_pipeline", flaky_create)

    report = await run_cycle([FakeSource([_candidate("t3_bad"), _candidate("t3_good")])])

    assert len(calls) == 2
    assert report.submitted == 1

    rows = {r.external_id: r for r in await _seen_rows(session)}
    assert rows["t3_bad"].status == SeenStatus.failed
    assert "orchestrator down" in rows["t3_bad"].skip_reason
    assert rows["t3_good"].status == SeenStatus.submitted


async def test_unsafe_candidate_is_recorded_and_skipped(
    submissions, set_active_runs, budget, moderation, session
):
    budget(max_pending_runs=99, max_per_cycle=2)
    moderation.set(
        lambda title, text: Verdict(safe=False, category="self_harm", reason="ideação descrita")
        if title == "ruim"
        else Verdict(safe=True)
    )

    report = await run_cycle([FakeSource([
        _candidate("t3_bad", title="ruim"),
        _candidate("t3_ok", title="boa"),
    ])])

    assert report.unsafe == 1
    assert report.submitted_ids == ["t3_ok"]

    rows = {r.external_id: r for r in await _seen_rows(session)}
    assert rows["t3_bad"].status == SeenStatus.filtered
    assert rows["t3_bad"].skip_reason == "unsafe:self_harm"


async def test_unsafe_candidate_does_not_consume_budget(
    submissions, set_active_runs, budget, moderation
):
    """A rejected story must not cost a slot — the next one takes it."""
    budget(max_pending_runs=99, max_per_cycle=2)
    moderation.set(
        lambda title, text: Verdict(safe=False, category="hate") if title == "ruim" else Verdict(safe=True)
    )

    report = await run_cycle([FakeSource([
        _candidate("t3_bad", title="ruim"),
        _candidate("t3_a", title="boa"),
        _candidate("t3_b", title="boa"),
    ])])

    assert report.submitted == 2
    assert report.submitted_ids == ["t3_a", "t3_b"]


async def test_moderation_only_runs_for_candidates_within_budget(
    submissions, set_active_runs, budget, moderation
):
    """Cost control: moderation is per-publication, not per-fetched-post."""
    budget(max_pending_runs=99, max_per_cycle=2)

    await run_cycle([FakeSource([_candidate(f"t3_{i}") for i in range(10)])])

    assert len(moderation.calls) == 2


async def test_length_filtered_candidates_are_never_moderated(
    submissions, set_active_runs, budget, moderation
):
    await run_cycle([FakeSource([_candidate("t3_short", chars=10)])])

    assert moderation.calls == []


async def test_moderation_outage_stops_cycle_without_consuming_candidates(
    submissions, set_active_runs, budget, moderation, session
):
    """"Could not check" must never read as either approval or rejection.

    Nothing is submitted, and nothing is recorded as seen — so the same stories
    are still available once the service is back.
    """
    budget(max_pending_runs=99, max_per_cycle=2)
    moderation.set(lambda title, text: ModerationError("connection refused"))

    report = await run_cycle([FakeSource([_candidate("t3_a"), _candidate("t3_b")])])

    assert report.moderation_unavailable is True
    assert report.submitted == 0
    assert report.unsafe == 0
    assert submissions == []
    assert await _seen_rows(session) == []


async def test_candidate_is_retried_after_moderation_recovers(
    submissions, set_active_runs, budget, moderation
):
    budget(max_pending_runs=99, max_per_cycle=2)
    moderation.set(lambda title, text: ModerationError("down"))
    await run_cycle([FakeSource([_candidate("t3_a")])])

    moderation.set(lambda title, text: Verdict(safe=True))
    report = await run_cycle([FakeSource([_candidate("t3_a")])])

    assert report.submitted == 1
    assert report.submitted_ids == ["t3_a"]


async def test_run_endpoint_returns_report(client, submissions, set_active_runs, budget, monkeypatch):
    monkeypatch.setattr(
        "src.content_scout.scout.build_sources", lambda: [FakeSource([_candidate("t3_a")])]
    )

    resp = await client.post("/scout/run")

    assert resp.status_code == 200
    body = resp.json()
    assert body["submitted"] == 1
    assert body["submitted_ids"] == ["t3_a"]


async def test_seen_endpoint_filters_by_status(client, submissions, set_active_runs, budget, monkeypatch):
    monkeypatch.setattr(
        "src.content_scout.scout.build_sources",
        lambda: [FakeSource([_candidate("t3_a"), _candidate("t3_short", chars=10)])],
    )
    await client.post("/scout/run")

    resp = await client.get("/scout/seen", params={"status": "filtered"})

    assert resp.status_code == 200
    body = resp.json()
    assert [i["external_id"] for i in body] == ["t3_short"]
    assert body[0]["skip_reason"] == "too_short:10"
