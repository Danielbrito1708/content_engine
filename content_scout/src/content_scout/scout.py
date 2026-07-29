import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import zip_longest

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.content_scout.clients.llm import (
    TAG_WEAK,
    ModerationClient,
    ModerationError,
    StoryQualityClient,
    StoryScore,
)
from src.content_scout.clients.orchestrator import OrchestratorClient
from src.content_scout.db.engine import AsyncSessionLocal
from src.content_scout.db.models import ArchiveCursor, ItemComment, SeenItem, SeenStatus
from src.content_scout.filters import content_fingerprint, evaluate
from src.content_scout.sources.base import (
    ArchiveCapableSource,
    Candidate,
    CommentCapableSource,
    CommentThread,
    Source,
)
from src.content_scout.sources.reddit import RedditSource
from src.core import settings

log = structlog.get_logger(__name__)


@dataclass
class ScoutReport:
    fetched: int = 0
    already_seen: int = 0
    filtered: int = 0
    unsafe: int = 0
    submitted: int = 0
    skipped_no_capacity: bool = False
    moderation_unavailable: bool = False
    active_runs: int = 0
    submitted_ids: list[str] = field(default_factory=list)
    comments_fetched: int = 0
    story_scored: int = 0
    weak_storytelling: int = 0
    story_quality_unavailable: bool = False
    already_running: bool = False
    #: Origin swept this cycle, ``None`` when none was due.
    archive_swept: str | None = None
    archive_fetched: int = 0
    #: The sweep reached the end of the listing and wrapped back to the top.
    archive_wrapped: bool = False
    #: Candidates dropped because the same story was already seen under another id.
    duplicate_story: int = 0


# One cycle at a time, process-wide. The periodic loop fires a cycle on startup,
# so a manual POST /scout/run right after a deploy used to run head-to-head with
# it: both walked the same candidate list, both passed the same dedup read, and
# the second commit died on the unique index — taking down a cycle that had
# already created pipeline runs.
_cycle_lock = asyncio.Lock()


async def _record(session, row: "SeenItem") -> bool:
    """Persist one audit row now, tolerating a duplicate from a racing writer.

    Committing per row rather than once at the end is the point: the batch commit
    meant a single conflict rolled back *every* row of the cycle, including the
    ones whose pipeline runs had already been created. Those stories came back as
    unseen on the next cycle and would have been published twice.
    """
    session.add(row)
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        log.info("scout_seen_duplicate", external_id=row.external_id)
        return False


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
            comments_limit=cfg.comments_limit,
            archive_time_filter=cfg.archive_time_filter,
            archive_limit=cfg.archive_limit,
        )
    ]


def interleave_by_origin(
    candidates: list[Candidate], usage: dict[str, int] | None = None
) -> list[Candidate]:
    """Round-robin across origins, least-served origin first.

    Sources are fetched and concatenated in config order, so taking the head of
    that list hands every slot to whichever subreddit happens to come first.
    Measured live: both submissions came from the first sub while the second
    contributed five candidates and won nothing — configuring more subreddits
    was decorative.

    Plain interleaving is not enough either. With a budget of 2 and three
    origins, ``a0, b0, c0, a1, …`` truncated at 2 yields ``a0, b0`` every single
    cycle: the third origin only gets a turn once the first two run dry.

    So ``usage`` — how many candidates each origin has had submitted, straight
    from the ``seen_items`` audit trail — orders the groups, least-served first.
    Ties break on name for determinism. The counter is derived, never stored, so
    it cannot drift out of sync with what was actually published.
    """
    usage = usage or {}
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.origin, []).append(candidate)

    ranked = sorted(groups.items(), key=lambda kv: (usage.get(kv[0], 0), kv[0]))

    ordered: list[Candidate] = []
    for row in zip_longest(*(items for _origin, items in ranked)):
        ordered.extend(c for c in row if c is not None)
    return ordered


def rank_by_story(
    candidates: list[Candidate], scores: dict[str, StoryScore], neutral: int
) -> list[Candidate]:
    """Order candidates by storytelling score, strongest first.

    A stable sort over the flat list is all this needs: ``interleave_by_origin``
    preserves each origin group's internal order, so sorting here turns that
    order into "best opening first" without touching the cross-origin fairness
    the interleave exists to provide. Ties keep the source's own ranking, which
    is the fallback signal when the model cannot separate two candidates.

    A candidate with no score sorts at ``neutral`` — the weak/strong threshold —
    rather than last. Sending it to the back would turn a model failure into a
    permanent handicap for a story nobody has actually judged, and the leftovers
    of every cycle are exactly the candidates that would inherit it.
    """
    def key(candidate: Candidate) -> int:
        score = scores.get(candidate.external_id)
        return -(score.score if score is not None else neutral)

    return sorted(candidates, key=key)


async def _submitted_per_origin(session) -> dict[str, int]:
    """How many candidates each origin has had published, from the audit trail."""
    result = await session.execute(
        select(SeenItem.origin, func.count())
        .where(SeenItem.status == SeenStatus.submitted)
        .group_by(SeenItem.origin)
    )
    return {origin: count for origin, count in result.all()}


async def _fetch_thread(
    sources_by_name: dict[str, Source], candidate: Candidate
) -> CommentThread | None:
    """Reactions to a candidate, or ``None`` when unavailable.

    Sources that do not implement the comment capability simply yield ``None`` —
    enrichment is optional by design, so adding a source never requires it.
    """
    source = sources_by_name.get(candidate.source)
    if not isinstance(source, CommentCapableSource):
        return None
    try:
        return await source.fetch_comments(candidate)
    except Exception as exc:  # noqa: BLE001 — enrichment is a bonus, never a blocker
        log.warning("scout_comments_failed", external_id=candidate.external_id, error=str(exc))
        return None


async def _pick_sweep_target(
    session, source: ArchiveCapableSource, interval_hours: float
) -> tuple[str, ArchiveCursor | None] | None:
    """The subreddit whose historical sweep is due, or ``None`` when none is.

    Never-swept subreddits go first, then the least recently swept — so a newly
    configured sub is picked up on the next cycle instead of waiting behind the
    rotation. Exactly one target per cycle: a sweep costs a full rate-limit
    window, and spending one per configured sub would multiply the cycle's floor.
    """
    subreddits = source.subreddits
    if not subreddits:
        return None

    result = await session.execute(
        select(ArchiveCursor).where(ArchiveCursor.origin.in_([f"r/{s}" for s in subreddits]))
    )
    cursors = {c.origin: c for c in result.scalars().all()}

    for subreddit in subreddits:
        if f"r/{subreddit}" not in cursors:
            return subreddit, None

    cutoff = datetime.now(timezone.utc) - timedelta(hours=interval_hours)
    due = [c for c in cursors.values() if c.last_swept_at <= cutoff]
    if not due:
        return None

    oldest = min(due, key=lambda c: c.last_swept_at)
    return oldest.origin.removeprefix("r/"), oldest


async def _sweep_archive(
    session, source: ArchiveCapableSource, subreddit: str, cursor: ArchiveCursor | None
) -> tuple[list[Candidate], bool]:
    """Read one page of ``subreddit``'s all-time top and advance its cursor.

    Returns the page's candidates and whether the listing wrapped. Wrapping is
    normal, not an error: the archive is finite, so a sweep that runs off the end
    resets to the top and starts over. By then everything it re-reads is already
    in ``seen_items``, so a lap costs requests but can never republish.

    The cursor is written even when the page yields nothing usable — otherwise a
    stretch of link posts would pin the sweep in place forever.
    """
    after = cursor.after_id if cursor is not None else None
    candidates, next_after = await source.fetch_archive(subreddit, after)
    wrapped = next_after is None

    if cursor is None:
        # ``pages_read`` is set explicitly rather than left to the column default:
        # that default is applied at INSERT, so the attribute is still None on a
        # pending object and the increment below would raise.
        cursor = ArchiveCursor(source=source.name, origin=f"r/{subreddit}", pages_read=0)
        session.add(cursor)

    cursor.after_id = next_after
    cursor.pages_read = 0 if wrapped else cursor.pages_read + 1
    cursor.last_swept_at = datetime.now(timezone.utc)
    await session.commit()

    return candidates, wrapped


async def _known_fingerprints(session, fingerprints: list[str]) -> set[str]:
    """Which of these bodies the scout has already evaluated, under any id.

    ``external_id`` only catches the identical post. The archive sweep reaches
    years back and into subs that repost each other, so the same story genuinely
    arrives twice with two ids — this is what sees that.
    """
    if not fingerprints:
        return set()
    result = await session.execute(
        select(SeenItem.content_fingerprint).where(
            SeenItem.content_fingerprint.in_(fingerprints)
        )
    )
    return set(result.scalars().all())


async def _run_sweep(
    session, sources: list[Source], interval_hours: float, report: ScoutReport
) -> list[Candidate]:
    """Sweep one archive page, if any source has one due.

    At most one sweep per cycle across all sources — the budget being protected
    is the shared rate-limit window, which does not care which source spends it.

    A failure here returns an empty page rather than propagating: the archive is
    a bonus supply of candidates on top of the live feeds, so losing it must not
    cost the cycle its regular fetch.
    """
    for source in sources:
        if not isinstance(source, ArchiveCapableSource):
            continue
        try:
            target = await _pick_sweep_target(session, source, interval_hours)
            if target is None:
                continue
            subreddit, cursor = target
            candidates, wrapped = await _sweep_archive(session, source, subreddit, cursor)
        except Exception as exc:  # noqa: BLE001 — the archive is a bonus, never a blocker
            log.warning("scout_archive_sweep_failed", error=str(exc))
            return []

        report.archive_swept = f"r/{subreddit}"
        report.archive_fetched = len(candidates)
        report.archive_wrapped = wrapped
        log.info(
            "scout_archive_swept",
            origin=report.archive_swept,
            fetched=len(candidates),
            wrapped=wrapped,
        )
        return candidates
    return []


async def _known_ids(session, external_ids: list[str]) -> set[str]:
    if not external_ids:
        return set()
    result = await session.execute(
        select(SeenItem.external_id).where(SeenItem.external_id.in_(external_ids))
    )
    return set(result.scalars().all())


async def run_cycle(sources: list[Source] | None = None) -> ScoutReport:
    """One scouting pass, serialized against any other pass in this process.

    A caller that arrives mid-cycle gets an empty report flagged
    ``already_running`` instead of waiting out a cycle that can take minutes —
    and instead of racing it, which is what produced duplicate submissions and a
    500 on the unique index.
    """
    if _cycle_lock.locked():
        log.info("scout_cycle_already_running")
        return ScoutReport(already_running=True)
    async with _cycle_lock:
        return await _run_cycle(sources)


async def _run_cycle(sources: list[Source] | None = None) -> ScoutReport:
    """One scouting pass: fetch, dedup, filter, submit within budget.

    Ordering matters. Capacity is checked before anything is submitted but after
    candidates are recorded, so a full queue costs nothing and loses nothing —
    the filtered/seen bookkeeping still happens and the next cycle starts from a
    smaller pile.
    """
    report = ScoutReport()
    # `is None`, not truthiness: an explicitly empty list means "no sources", and
    # falling back to the configured ones there turns a caller asking for nothing
    # into live Reddit traffic.
    sources = build_sources() if sources is None else sources
    scout_cfg = settings.CONFIG.scout
    filter_cfg = settings.CONFIG.filters
    reddit_cfg = settings.CONFIG.reddit

    candidates: list[Candidate] = []
    for source in sources:
        candidates.extend(await source.fetch())
    report.fetched = len(candidates)

    # Candidates carry their source's name, not the object; enrichment needs the
    # object back to ask it for comments.
    sources_by_name = {source.name: source for source in sources}

    orchestrator = OrchestratorClient()

    async with AsyncSessionLocal() as session:
        # The historical sweep rides along with the regular fetch, before the
        # capacity check, for the same reason the fetch does: what it costs is a
        # rate-limit window, and skipping it on a full queue would mean the
        # archive only ever advances on quiet cycles.
        if reddit_cfg.archive_interval_hours > 0:
            archived = await _run_sweep(session, sources, reddit_cfg.archive_interval_hours, report)
            candidates.extend(archived)

        seen = await _known_ids(session, [c.external_id for c in candidates])
        fingerprints = await _known_fingerprints(
            session, [f for f in (content_fingerprint(c.text) for c in candidates) if f]
        )

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
            )
            # Fingerprint dedup runs after the cheap length check and before
            # anything that costs a model call: a repost is the cheapest possible
            # rejection and should never reach moderation or scoring.
            fingerprint = content_fingerprint(candidate.text)
            if reason is None and fingerprint is not None and fingerprint in fingerprints:
                reason = "duplicate_story"
                report.duplicate_story += 1
            if fingerprint is not None:
                # Added even when this candidate is being rejected: two copies
                # arriving in the *same* cycle are not in the database yet, and
                # without this the second would sail through.
                fingerprints.add(fingerprint)

            if reason:
                report.filtered += 1
                await _record(
                    session,
                    _seen_row(candidate, SeenStatus.filtered, skip_reason=reason),
                )
                continue
            fresh.append(candidate)

        report.active_runs = await orchestrator.count_active_runs()
        capacity = scout_cfg.max_pending_runs - report.active_runs
        if capacity <= 0:
            report.skipped_no_capacity = True
            log.info("scout_no_capacity", active_runs=report.active_runs)
            return report

        budget = min(capacity, scout_cfg.max_per_cycle)
        moderation = ModerationClient()
        usage = await _submitted_per_origin(session)

        # Story quality runs over *every* fresh candidate, not just the ones about
        # to be published — it is what decides which those are, so scoring only
        # the head of the list would be circular. That is affordable because the
        # whole cycle is one batched call; see StoryQualityClient. It runs after
        # the capacity check for the same reason moderation does: a full queue
        # publishes nothing, so it should cost nothing.
        story_scores: dict[str, StoryScore] = {}
        if scout_cfg.story_quality:
            story_scores = await StoryQualityClient().score(fresh)
            report.story_scored = len(story_scores)
            # An outage here is recorded but never fatal: unlike moderation, this
            # gates nothing. The cycle carries on selecting by source ranking.
            report.story_quality_unavailable = bool(fresh) and not story_scores
            report.weak_storytelling = sum(
                1
                for score in story_scores.values()
                if score.tag(scout_cfg.min_story_score) == TAG_WEAK
            )

        ordered = interleave_by_origin(
            rank_by_story(fresh, story_scores, scout_cfg.min_story_score), usage
        )

        # Walk past the budget: rejected candidates don't consume a slot, so the
        # list has to be walked until the budget is actually filled. Moderation
        # runs here rather than in the filter pass so it only ever costs a call
        # for candidates that were genuinely about to be published — a handful
        # per cycle instead of one per fetched post.
        for candidate in ordered:
            if report.submitted >= budget:
                break

            story = story_scores.get(candidate.external_id)

            try:
                verdict = await moderation.check(candidate.title, candidate.text)
            except ModerationError as exc:
                # Not recorded as seen: an outage must not permanently discard a
                # story. Stop the cycle — if the service is down, every remaining
                # candidate would fail the same way.
                report.moderation_unavailable = True
                log.error("scout_moderation_unavailable", error=str(exc))
                break

            if not verdict.safe:
                report.unsafe += 1
                report.filtered += 1
                await _record(
                    session,
                    _seen_row(
                        candidate,
                        SeenStatus.filtered,
                        skip_reason=verdict.as_skip_reason(),
                        story=story,
                        min_story_score=scout_cfg.min_story_score,
                    ),
                )
                log.info(
                    "scout_rejected_unsafe",
                    external_id=candidate.external_id,
                    category=verdict.category,
                    reason=verdict.reason,
                )
                continue

            # Comment enrichment costs a full rate-limit window per candidate, so
            # it runs here for the same reason moderation does: only for posts
            # that are actually about to be published, a couple per cycle instead
            # of one per fetched post.
            thread = (
                await _fetch_thread(sources_by_name, candidate)
                if scout_cfg.fetch_comments
                else None
            )
            if thread is not None:
                report.comments_fetched += 1

            metadata = candidate.to_metadata()
            if thread is not None:
                metadata["comment_count"] = thread.total
            if story is not None:
                # The refiner is told to open on a strong hook. Knowing whether
                # this post already has one — and which line it is — is the
                # difference between polishing a hook and having to invent one.
                metadata.update(story.as_metadata(scout_cfg.min_story_score))

            try:
                run_id = await orchestrator.create_pipeline(
                    script=candidate.text, metadata=metadata
                )
            except Exception as exc:  # noqa: BLE001 — one bad submit must not end the cycle
                log.warning("scout_submit_failed", external_id=candidate.external_id, error=str(exc))
                await _record(
                    session,
                    _seen_row(
                        candidate,
                        SeenStatus.failed,
                        skip_reason=str(exc)[:255],
                        thread=thread,
                        max_comments=scout_cfg.max_comments_stored,
                        story=story,
                        min_story_score=scout_cfg.min_story_score,
                    ),
                )
                continue

            report.submitted += 1
            report.submitted_ids.append(candidate.external_id)
            # Committed immediately, while the run id is in hand: anything that
            # fails later in this cycle must not be able to erase the record of a
            # story that is already in the pipeline.
            await _record(
                session,
                _seen_row(
                    candidate,
                    SeenStatus.submitted,
                    pipeline_run_id=run_id,
                    thread=thread,
                    max_comments=scout_cfg.max_comments_stored,
                    story=story,
                    min_story_score=scout_cfg.min_story_score,
                ),
            )
            log.info(
                "scout_submitted",
                external_id=candidate.external_id,
                origin=candidate.origin,
                run_id=str(run_id),
                story_score=story.score if story else None,
                story_tag=story.tag(scout_cfg.min_story_score) if story else None,
            )

    return report


def _seen_row(candidate: Candidate, status: SeenStatus, skip_reason: str | None = None,
              pipeline_run_id=None, thread: CommentThread | None = None,
              max_comments: int = 0, story: StoryScore | None = None,
              min_story_score: int = 0) -> SeenItem:
    """Build the audit row, with comment enrichment when it was gathered.

    ``comment_count`` stays ``None`` for candidates that were never enriched —
    filtered ones never are — so the column distinguishes "no replies" from "not
    looked at". Only the first ``max_comments`` bodies are kept: the count is the
    signal, the bodies are a sample.

    The story columns follow the same rule: ``None`` throughout when ``story`` is
    absent, which covers items rejected by the cheap filters and every item in a
    cycle where the scoring call failed. The tag is derived here rather than
    stored by the client so the threshold applies at write time — moving it later
    only affects new rows, and the raw ``story_score`` stays there to re-derive
    the old ones.
    """
    return SeenItem(
        source=candidate.source,
        external_id=candidate.external_id,
        origin=candidate.origin,
        title=candidate.title[:500],
        url=candidate.url,
        char_count=candidate.char_count,
        author=candidate.extra.get("author") or None,
        # Written for every row, including rejected ones: a repost of a story that
        # was filtered for length is still a repost, and recording the fingerprint
        # is what lets the next copy be recognised without re-deriving it.
        content_fingerprint=content_fingerprint(candidate.text),
        comment_count=thread.total if thread is not None else None,
        has_hook=story.hook if story is not None else None,
        story_score=story.score if story is not None else None,
        story_tag=story.tag(min_story_score) if story is not None else None,
        hook_line=story.hook_line if story is not None else None,
        story_reason=story.reason[:255] if story is not None and story.reason else None,
        status=status,
        skip_reason=skip_reason,
        pipeline_run_id=pipeline_run_id,
        comments=[
            ItemComment(
                external_id=c.external_id,
                author=c.author or None,
                text=c.text,
                position=c.position,
                published=c.published or None,
            )
            for c in (thread.comments[:max_comments] if thread is not None else [])
        ],
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
                unsafe=report.unsafe,
                submitted=report.submitted,
                comments_fetched=report.comments_fetched,
                story_scored=report.story_scored,
                weak_storytelling=report.weak_storytelling,
                archive_swept=report.archive_swept,
                archive_fetched=report.archive_fetched,
                archive_wrapped=report.archive_wrapped,
                duplicate_story=report.duplicate_story,
                no_capacity=report.skipped_no_capacity,
                moderation_unavailable=report.moderation_unavailable,
                story_quality_unavailable=report.story_quality_unavailable,
            )
        except Exception as exc:  # noqa: BLE001 — the loop must outlive any single failure
            log.error("scout_cycle_failed", error=str(exc), exc_info=True)
        await asyncio.sleep(interval)
