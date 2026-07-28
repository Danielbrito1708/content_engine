"""What the pipeline does when something goes wrong overnight.

Queue-full pauses, restart recovery and background selection — the three paths
that decide whether the stack survives a night without a human.
"""

import asyncio
import uuid
from datetime import datetime, timezone

import respx
from httpx import Response

from src.orchestrator.db.models import PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.worker import (
    _schedule,
    background_key_for,
    recover_interrupted_runs,
    retry_pending_schedules,
)

QUEUE_FULL = Response(
    429, json={"detail": {"error": "buffer_queue_full", "message": "cheia", "pending_count": 10}}
)


async def _make_run(session, status=PipelineStatus.pending, classification=None):
    run = PipelineRun(raw_script="Roteiro.", status=status, classification=classification or {})
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _make_part(session, run_id, number=1, video_key=None, scheduled_at=None):
    part = PipelinePart(
        run_id=run_id,
        part_number=number,
        script=f"Parte {number}.",
        video_key=video_key,
        scheduled_at=scheduled_at,
    )
    session.add(part)
    await session.commit()
    await session.refresh(part)
    return part


# ── Buffer queue full ──────────────────────────────────────────────────────

@respx.mock
async def test_queue_full_parks_the_run_instead_of_failing_it(session):
    """The videos are rendered and fine — the queue just has no room. Failing
    here threw away an hour of LLM, speech and render work."""
    run = await _make_run(session)
    part = await _make_part(session, run.id, video_key="outputs/abc.mp4")
    respx.post("http://tiktok_poster:8000/schedule").mock(return_value=QUEUE_FULL)

    scheduled = await _schedule(session, run)

    await session.refresh(run)
    await session.refresh(part)
    assert scheduled is False
    assert run.status == PipelineStatus.scheduling
    assert run.error is None
    assert part.video_key == "outputs/abc.mp4"


@respx.mock
async def test_parked_run_counts_as_active_capacity(session):
    """``scheduling`` is one of the states the scout reads as occupied — that is
    the backpressure link: ingestion stops on its own while Buffer is full."""
    from src.orchestrator.worker import ACTIVE_STATUSES

    run = await _make_run(session)
    await _make_part(session, run.id, video_key="outputs/abc.mp4")
    respx.post("http://tiktok_poster:8000/schedule").mock(return_value=QUEUE_FULL)

    await _schedule(session, run)

    await session.refresh(run)
    assert run.status in ACTIVE_STATUSES


@respx.mock
async def test_retry_pass_drains_a_parked_run_once_the_queue_opens(session):
    run = await _make_run(session, status=PipelineStatus.scheduling)
    part = await _make_part(session, run.id, video_key="outputs/abc.mp4")
    respx.post("http://tiktok_poster:8000/schedule").mock(
        return_value=Response(200, json={"scheduled_at": "2026-06-01T08:00:00Z",
                                         "buffer_update_id": "buf_9"})
    )

    drained = await retry_pending_schedules()

    await session.refresh(run)
    await session.refresh(part)
    # The sweep is table-wide by design, so the count is a floor, not an equality.
    assert drained >= 1
    assert run.status == PipelineStatus.scheduled
    assert part.tiktok_video_id == "buf_9"


@respx.mock
async def test_schedule_never_reposts_a_part_that_is_already_booked(session):
    """The retry pass calls this repeatedly; a part with a slot must not be
    offered twice, or the same video is published twice."""
    run = await _make_run(session)
    booked = await _make_part(
        session, run.id, number=1, video_key="outputs/a.mp4",
        scheduled_at=datetime(2026, 6, 1, 8, tzinfo=timezone.utc),
    )
    await _make_part(session, run.id, number=2, video_key="outputs/b.mp4")

    route = respx.post("http://tiktok_poster:8000/schedule").mock(
        return_value=Response(200, json={"scheduled_at": "2026-06-01T20:00:00Z",
                                         "buffer_update_id": "buf_2"})
    )

    await _schedule(session, run)

    await session.refresh(booked)
    assert route.call_count == 1
    assert booked.tiktok_video_id is None


# ── restart recovery ───────────────────────────────────────────────────────

async def test_restart_fails_a_run_that_was_mid_render(session):
    """No task survives a restart, so an active run at startup is ownerless.
    Left alone it stayed ``processing`` forever and ate a scout capacity slot."""
    run = await _make_run(session, status=PipelineStatus.processing)
    await _make_part(session, run.id, video_key=None)

    await recover_interrupted_runs()

    await session.refresh(run)
    assert run.status == PipelineStatus.failed
    assert "restart" in run.error


async def test_restart_resumes_a_run_that_only_owed_scheduling(session, monkeypatch):
    """All parts rendered means the run owes one cheap call, not a re-render."""
    resumed_ids = []

    async def capture(run_id):
        resumed_ids.append(run_id)

    monkeypatch.setattr("src.orchestrator.worker._schedule_in_background", capture)

    run = await _make_run(session, status=PipelineStatus.scheduling)
    await _make_part(session, run.id, video_key="outputs/abc.mp4")

    await recover_interrupted_runs()
    await asyncio.sleep(0)  # let the resume task actually start

    await session.refresh(run)
    assert run.id in resumed_ids
    assert run.status != PipelineStatus.failed


async def test_restart_leaves_finished_runs_alone(session):
    run = await _make_run(session, status=PipelineStatus.scheduled)

    await recover_interrupted_runs()

    await session.refresh(run)
    assert run.status == PipelineStatus.scheduled
    assert run.error is None


async def test_restart_fails_a_run_with_no_parts_at_all(session):
    """Interrupted before refine even produced parts: nothing to resume."""
    run = await _make_run(session, status=PipelineStatus.refining)

    await recover_interrupted_runs()

    await session.refresh(run)
    assert run.status == PipelineStatus.failed


# ── background selection ───────────────────────────────────────────────────

async def test_background_comes_from_the_clip_library(monkeypatch):
    async def fake_list(_bucket, prefix):
        assert prefix == "assets/backgrounds/"
        return ["assets/backgrounds/bg_000.mp4", "assets/backgrounds/bg_001.mp4"]

    monkeypatch.setattr("src.orchestrator.worker.list_keys", fake_list)

    key = await background_key_for(uuid.uuid4(), 1)

    assert key.startswith("assets/backgrounds/bg_")


async def test_background_falls_back_to_the_fixed_key_when_library_is_empty(monkeypatch):
    """A bucket nobody filled still renders, on the single old key, instead of
    failing at the last step."""
    async def empty(_bucket, _prefix):
        return []

    monkeypatch.setattr("src.orchestrator.worker.list_keys", empty)

    key = await background_key_for(uuid.uuid4(), 1)

    assert key == "assets/background.mp4"
