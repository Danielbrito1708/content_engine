import asyncio
import uuid

from sqlalchemy import select
from structlog import get_logger

from src.orchestrator.clients.blender import BlenderClient
from src.orchestrator.clients.llm import LLMClient
from src.orchestrator.clients.tiktok import TikTokClient
from src.orchestrator.clients.tts import TTSClient
from src.orchestrator.db.engine import AsyncSessionLocal
from src.orchestrator.db.models import PartStatus, PipelinePart, PipelineRun, PipelineStatus

log = get_logger(__name__)

_RENDER_POLL_INTERVAL = 10
_RENDER_TIMEOUT = 3600


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

    result = await session.execute(
        select(PipelinePart).where(PipelinePart.run_id == run.id).order_by(PipelinePart.part_number)
    )
    parts = result.scalars().all()

    for part in parts:
        await _run_tts(session, part, run)
        await _run_render(session, part, run)

    log.info("all parts processed", run_id=str(run.id))


async def _run_tts(session, part: PipelinePart, run: PipelineRun) -> None:
    log.info("generating audio", run_id=str(run.id), part=part.part_number)
    part.status = PartStatus.tts_running
    await session.commit()

    audio_key = await TTSClient().generate(
        text=part.script,
        run_id=str(run.id),
        part_number=part.part_number,
    )

    part.audio_key = audio_key
    part.status = PartStatus.tts_done
    await session.commit()
    log.info("audio ready", run_id=str(run.id), part=part.part_number, key=audio_key)


async def _run_render(session, part: PipelinePart, run: PipelineRun) -> None:
    # TODO: blender_worker currently receives video_id + template_id from its own DB.
    # The orchestrator will need to register the audio as a video asset in blender_worker
    # before creating the job. This integration will be defined when blender_worker's
    # API is extended to accept direct asset keys.
    log.info("render not yet integrated", run_id=str(run.id), part=part.part_number)
    part.status = PartStatus.render_pending
    await session.commit()


async def _schedule(session, run: PipelineRun) -> None:
    log.info("scheduling posts", run_id=str(run.id))
    run.status = PipelineStatus.scheduling
    await session.commit()

    result = await session.execute(
        select(PipelinePart).where(PipelinePart.run_id == run.id).order_by(PipelinePart.part_number)
    )
    parts = result.scalars().all()

    tiktok = TikTokClient()
    for part in parts:
        if part.video_key is None:
            continue
        data = await tiktok.schedule(
            video_key=part.video_key,
            classification=run.classification or {},
            part_number=part.part_number,
            series_id=str(run.id),
        )
        part.scheduled_at = data.get("scheduled_at")
        part.tiktok_video_id = data.get("tiktok_video_id")

    run.status = PipelineStatus.scheduled
    await session.commit()
    log.info("pipeline scheduled", run_id=str(run.id))
