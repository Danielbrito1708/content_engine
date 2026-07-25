import uuid

import pytest
from sqlalchemy import select

from src.content_scout.clients.orchestrator import OrchestratorClient
from src.content_scout.db.models import SeenItem, SeenStatus
from src.content_scout.scout import run_cycle
from src.content_scout.sources.base import Candidate
from src.core import settings


class FakeSource:
    name = "reddit"

    def __init__(self, candidates):
        self._candidates = candidates

    async def fetch(self):
        return list(self._candidates)


def _candidate(external_id: str, chars: int = 1000, title: str = "Título") -> Candidate:
    return Candidate(
        source="reddit",
        external_id=external_id,
        origin="r/desabafos",
        title=title,
        text="a" * chars,
        url=f"https://reddit.com/{external_id}",
    )


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
