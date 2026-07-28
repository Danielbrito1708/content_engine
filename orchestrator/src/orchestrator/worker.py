import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select
from structlog import get_logger

from src.core import settings
from src.orchestrator.backgrounds import pick_background
from src.orchestrator.clients.blender import BlenderClient
from src.orchestrator.clients.llm import LLMClient
from src.orchestrator.clients.tiktok import BufferQueueFull, TikTokClient
from src.orchestrator.clients.tts import TTSClient
from src.orchestrator.db.engine import AsyncSessionLocal
from src.orchestrator.db.models import PartStatus, PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.storage.client import list_keys

log = get_logger(__name__)

#: States that mean "work is supposed to be happening". The scout counts these as
#: occupied capacity, so nothing may sit in one of them without an owner.
ACTIVE_STATUSES = (
    PipelineStatus.pending,
    PipelineStatus.refining,
    PipelineStatus.refined,
    PipelineStatus.processing,
    PipelineStatus.scheduling,
)


async def run_pipeline(run_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(PipelineRun, run_id)
        if run is None:
            log.error("pipeline_run not found", run_id=str(run_id))
            return

        try:
            await _refine(session, run)
            await _process_all_parts(session, run)
            await _schedule(session, run)
        except Exception as exc:
            run.status = PipelineStatus.failed
            run.error = str(exc)
            log.error("pipeline failed", run_id=str(run_id), error=str(exc))
            await session.commit()


async def _refine(session, run: PipelineRun) -> None:
    log.info("refining script", run_id=str(run.id))
    run.status = PipelineStatus.refining
    await session.commit()

    result = await LLMClient().refine(run.raw_script, run.input_metadata or {})

    run.refined_script = "\n\n".join(result.parts)
    run.classification = result.classification
    run.parts_count = len(result.parts)
    run.status = PipelineStatus.refined
    await session.commit()

    for i, script in enumerate(result.parts, start=1):
        session.add(PipelinePart(run_id=run.id, part_number=i, script=script))
    await session.commit()

    log.info("script refined", run_id=str(run.id), parts=run.parts_count)


async def _process_all_parts(session, run: PipelineRun) -> None:
    run.status = PipelineStatus.processing
    await session.commit()

    for part in await _parts_of(session, run):
        await _run_tts(session, part, run)
        await _run_render(session, part, run)

    log.info("all parts processed", run_id=str(run.id))


async def _run_tts(session, part: PipelinePart, run: PipelineRun) -> None:
    log.info("generating audio", run_id=str(run.id), part=part.part_number)
    part.status = PartStatus.tts_running
    await session.commit()

    audio_key, srt_key = await TTSClient().generate(
        text=part.script,
        run_id=str(run.id),
        part_number=part.part_number,
    )

    part.audio_key = audio_key
    part.srt_key = srt_key
    part.status = PartStatus.tts_done
    await session.commit()
    log.info("audio ready", run_id=str(run.id), part=part.part_number, audio=audio_key, srt=srt_key)


async def background_key_for(run_id: uuid.UUID, part_number: int) -> str:
    """The background clip this part renders over.

    Falls back to the single ``background_video_key`` when nothing is published
    under the prefix: a bucket that was never filled still renders, instead of
    failing at the last step. The fixed key is the old behaviour, kept as a floor.
    """
    template = settings.CONFIG.template
    prefix = str(getattr(template, "background_prefix", "") or "")

    if prefix:
        keys = await list_keys(settings.CONFIG.storage.bucket, prefix)
        if keys:
            return pick_background(keys, str(run_id), part_number)
        log.warning("no background clips under prefix", prefix=prefix)

    return template.background_video_key


async def _run_render(session, part: PipelinePart, run: PipelineRun) -> None:
    log.info("starting render", run_id=str(run.id), part=part.part_number)
    part.status = PartStatus.render_pending
    await session.commit()

    background_key = await background_key_for(run.id, part.part_number)

    blender = BlenderClient()
    video_id = await blender.create_video(
        video_file_key=background_key,
        music_key=settings.CONFIG.template.music_key,
        voice_key=part.audio_key,
        subtitle_key=part.srt_key,
    )

    template_id = uuid.UUID(settings.env.blender_template_id)
    job_id = await blender.create_job(video_id=video_id, template_id=template_id)

    part.blender_job_id = job_id
    part.status = PartStatus.render_running
    await session.commit()

    result = await blender.poll_job(job_id)
    if result["status"] == "failed":
        raise RuntimeError(f"render job failed: {result.get('error')}")

    part.video_key = result["output_key"]
    part.status = PartStatus.render_done
    await session.commit()
    log.info(
        "render done",
        run_id=str(run.id),
        part=part.part_number,
        key=part.video_key,
        background=background_key,
    )


async def _schedule(session, run: PipelineRun) -> bool:
    """Hand every rendered part to the poster. ``False`` means the queue is full.

    Idempotent on purpose: parts that already carry a ``scheduled_at`` are
    skipped, so this can run again — after a restart, or once the queue drains —
    without double-posting what is already booked.
    """
    log.info("scheduling posts", run_id=str(run.id))
    run.status = PipelineStatus.scheduling
    await session.commit()

    tiktok = TikTokClient()

    for part in await _parts_of(session, run):
        if part.video_key is None:
            log.warning("part has no video_key, skipping schedule", part=part.part_number)
            continue
        if part.scheduled_at is not None:
            continue

        try:
            data = await tiktok.schedule(
                video_key=part.video_key,
                classification=run.classification or {},
                part_number=part.part_number,
                series_id=str(run.id),
            )
        except BufferQueueFull as exc:
            # Not a failure: the run keeps its rendered videos and stays in
            # ``scheduling``, which the scout reads as occupied capacity. That is
            # the backpressure link that was missing — ingestion now stops on its
            # own while the queue is full, instead of feeding it renders that die
            # at the very last step after everything has already been paid for.
            log.info(
                "buffer queue full, run waiting for a slot",
                run_id=str(run.id),
                part=part.part_number,
                pending=exc.pending_count,
            )
            return False

        raw_ts = data.get("scheduled_at")
        part.scheduled_at = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")) if raw_ts else None
        part.tiktok_video_id = data.get("buffer_update_id")
        await session.commit()

    run.status = PipelineStatus.scheduled
    await session.commit()
    log.info("pipeline scheduled", run_id=str(run.id))
    return True


async def _parts_of(session, run: PipelineRun) -> list[PipelinePart]:
    result = await session.execute(
        select(PipelinePart).where(PipelinePart.run_id == run.id).order_by(PipelinePart.part_number)
    )
    return list(result.scalars().all())


async def recover_interrupted_runs() -> dict[str, int]:
    """Reconcile runs left mid-flight by a restart. Runs once, at startup.

    The pipeline executes inside a FastAPI background task, which dies with the
    process. Nothing used to notice: a run interrupted mid-render stayed
    ``processing`` forever, and because the scout counts that as occupied
    capacity, five interrupted runs stopped ingestion for good. Any active state
    at startup is by definition ownerless — no task survives a restart.

    Runs whose parts are all rendered only owe a scheduling call, so they are
    picked back up. The rest are failed with an explicit reason: re-running the
    pipeline from the top would duplicate parts and pay for the LLM and the
    speech a second time.
    """
    resumed = 0
    failed = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PipelineRun).where(PipelineRun.status.in_(ACTIVE_STATUSES))
        )
        runs = list(result.scalars().all())

        for run in runs:
            parts = await _parts_of(session, run)
            if parts and all(p.video_key is not None for p in parts):
                resumed += 1
                log.info("resuming interrupted run", run_id=str(run.id), status=run.status.value)
                asyncio.create_task(_schedule_in_background(run.id))
            else:
                failed += 1
                run.status = PipelineStatus.failed
                run.error = "interrompido por restart do orchestrator"
                log.warning("failing interrupted run", run_id=str(run.id))
                await session.commit()

    if runs:
        log.info("startup recovery done", resumed=resumed, failed=failed)
    return {"resumed": resumed, "failed": failed}


async def retry_pending_schedules() -> int:
    """Re-offer runs parked on a full queue. Returns how many drained.

    This is what makes ``BufferQueueFull`` a pause rather than a loss: the run
    waits in ``scheduling`` until Buffer has room, and this pass — not a person —
    picks it back up.
    """
    drained = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PipelineRun).where(PipelineRun.status == PipelineStatus.scheduling)
        )
        for run in list(result.scalars().all()):
            try:
                if await _schedule(session, run):
                    drained += 1
            except Exception as exc:  # noqa: BLE001 — one stuck run must not end the sweep
                log.error("retry schedule failed", run_id=str(run.id), error=str(exc))
                await session.rollback()

    return drained


async def _schedule_in_background(run_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(PipelineRun, run_id)
        if run is None:
            return
        try:
            await _schedule(session, run)
        except Exception as exc:  # noqa: BLE001 — mirrors run_pipeline's own guard
            run.status = PipelineStatus.failed
            run.error = str(exc)
            log.error("resumed run failed", run_id=str(run_id), error=str(exc))
            await session.commit()


async def maintenance_loop() -> None:
    """Periodic drain of runs parked on a full Buffer queue.

    Outlives any single failure for the same reason the scout's loop does: a
    night with nobody watching is exactly when it must not stop.
    """
    pipeline_cfg = getattr(settings.CONFIG, "pipeline", None)
    interval = int(getattr(pipeline_cfg, "retry_interval_seconds", 900) if pipeline_cfg else 900)
    log.info("maintenance_loop_started", interval_seconds=interval)

    while True:
        await asyncio.sleep(interval)
        try:
            drained = await retry_pending_schedules()
            if drained:
                log.info("maintenance drained runs", runs=drained)
        except Exception as exc:  # noqa: BLE001 — the loop must survive the night
            log.error("maintenance_loop_failed", error=str(exc))
