import asyncio
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select

from src.content_scout.clients.orchestrator import OrchestratorClient
from src.content_scout.db.engine import AsyncSessionLocal
from src.content_scout.db.models import SeenItem, SeenStatus
from src.content_scout.filters import evaluate
from src.content_scout.sources.base import Candidate, Source
from src.content_scout.sources.reddit import RedditSource
from src.core import settings

log = structlog.get_logger(__name__)


@dataclass
class ScoutReport:
    fetched: int = 0
    already_seen: int = 0
    filtered: int = 0
    submitted: int = 0
    skipped_no_capacity: bool = False
    active_runs: int = 0
    submitted_ids: list[str] = field(default_factory=list)


def build_sources() -> list[Source]:
    """Assemble the enabled sources.

    Only Reddit today. YouTube plugs in here as a second entry — the scout below
    treats every source identically, so adding one changes nothing else.
    """
    cfg = settings.CONFIG.reddit
    subreddits = [s.strip() for s in str(cfg.subreddits).split(",") if s.strip()]
    return [
        RedditSource(
            base_url=cfg.base_url,
            subreddits=subreddits,
            time_filter=cfg.time_filter,
            limit_per_subreddit=cfg.limit_per_subreddit,
            user_agent=settings.env.user_agent,
            timeout=cfg.request_timeout,
            request_delay=cfg.request_delay_seconds,
        )
    ]


async def _known_ids(session, external_ids: list[str]) -> set[str]:
    if not external_ids:
        return set()
    result = await session.execute(
        select(SeenItem.external_id).where(SeenItem.external_id.in_(external_ids))
    )
    return set(result.scalars().all())


async def run_cycle(sources: list[Source] | None = None) -> ScoutReport:
    """One scouting pass: fetch, dedup, filter, submit within budget.

    Ordering matters. Capacity is checked before anything is submitted but after
    candidates are recorded, so a full queue costs nothing and loses nothing —
    the filtered/seen bookkeeping still happens and the next cycle starts from a
    smaller pile.
    """
    report = ScoutReport()
    sources = sources or build_sources()
    scout_cfg = settings.CONFIG.scout
    filter_cfg = settings.CONFIG.filters
    blocklist = [t.strip() for t in str(filter_cfg.blocklist).split(",") if t.strip()]

    candidates: list[Candidate] = []
    for source in sources:
        candidates.extend(await source.fetch())
    report.fetched = len(candidates)

    orchestrator = OrchestratorClient()

    async with AsyncSessionLocal() as session:
        seen = await _known_ids(session, [c.external_id for c in candidates])

        fresh: list[Candidate] = []
        for candidate in candidates:
            if candidate.external_id in seen:
                report.already_seen += 1
                continue
            # Guard against the same post appearing in two feeds in one cycle.
            seen.add(candidate.external_id)

            reason = evaluate(
                candidate,
                min_chars=filter_cfg.min_chars,
                max_chars=filter_cfg.max_chars,
                blocklist=blocklist,
            )
            if reason:
                report.filtered += 1
                session.add(_seen_row(candidate, SeenStatus.filtered, skip_reason=reason))
                continue
            fresh.append(candidate)

        await session.commit()

        report.active_runs = await orchestrator.count_active_runs()
        capacity = scout_cfg.max_pending_runs - report.active_runs
        if capacity <= 0:
            report.skipped_no_capacity = True
            log.info("scout_no_capacity", active_runs=report.active_runs)
            return report

        budget = min(capacity, scout_cfg.max_per_cycle, len(fresh))
        for candidate in fresh[:budget]:
            try:
                run_id = await orchestrator.create_pipeline(
                    script=candidate.text, metadata=candidate.to_metadata()
                )
            except Exception as exc:  # noqa: BLE001 — one bad submit must not end the cycle
                log.warning("scout_submit_failed", external_id=candidate.external_id, error=str(exc))
                session.add(_seen_row(candidate, SeenStatus.failed, skip_reason=str(exc)[:255]))
                continue

            report.submitted += 1
            report.submitted_ids.append(candidate.external_id)
            session.add(_seen_row(candidate, SeenStatus.submitted, pipeline_run_id=run_id))
            log.info(
                "scout_submitted",
                external_id=candidate.external_id,
                origin=candidate.origin,
                run_id=str(run_id),
            )

        await session.commit()

    return report


def _seen_row(candidate: Candidate, status: SeenStatus, skip_reason: str | None = None,
              pipeline_run_id=None) -> SeenItem:
    return SeenItem(
        source=candidate.source,
        external_id=candidate.external_id,
        origin=candidate.origin,
        title=candidate.title[:500],
        url=candidate.url,
        char_count=candidate.char_count,
        status=status,
        skip_reason=skip_reason,
        pipeline_run_id=pipeline_run_id,
    )


async def scout_loop() -> None:
    """Periodic driver, started on app startup.

    No durable scheduler needed here: ``seen_items`` makes a cycle idempotent, so
    a container restart at worst repeats a pass that finds nothing new.
    """
    interval = settings.CONFIG.scout.interval_seconds
    log.info("scout_loop_started", interval_seconds=interval)
    while True:
        try:
            report = await run_cycle()
            log.info(
                "scout_cycle_done",
                fetched=report.fetched,
                already_seen=report.already_seen,
                filtered=report.filtered,
                submitted=report.submitted,
                no_capacity=report.skipped_no_capacity,
            )
        except Exception as exc:  # noqa: BLE001 — the loop must outlive any single failure
            log.error("scout_cycle_failed", error=str(exc), exc_info=True)
        await asyncio.sleep(interval)
