import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from structlog import get_logger

from src.core import settings
from src.orchestrator.clients.blender import BlenderClient
from src.orchestrator.clients.llm import LLMClient
from src.orchestrator.clients.tiktok import TikTokClient
from src.orchestrator.clients.tts import TTSClient
from src.orchestrator.db.engine import AsyncSessionLocal
from src.orchestrator.db.models import PartStatus, PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.storage.client import upload_bytes

log = get_logger(__name__)


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


async def _narration_rate() -> str | None:
    """`narration.rate` from the template, or None to let the tts_service decide.

    A template without a narration block — or an unreachable blender_worker — is not
    worth failing a run over: the pipeline falls back to the tts_service's TTS_RATE.
    """
    template_id = uuid.UUID(settings.env.blender_template_id)
    try:
        config = await BlenderClient().get_template_config(template_id)
    except Exception as exc:
        log.warning("could not read template config, using tts_service default rate", error=str(exc))
        return None

    rate = (config.get("narration") or {}).get("rate")
    if rate is None:
        log.info("template has no narration.rate, using tts_service default")
    return rate


async def _process_all_parts(session, run: PipelineRun) -> None:
    run.status = PipelineStatus.processing
    await session.commit()

    result = await session.execute(
        select(PipelinePart).where(PipelinePart.run_id == run.id).order_by(PipelinePart.part_number)
    )
    parts = result.scalars().all()

    # Fetched once per run: the template is the same for every part.
    rate = await _narration_rate()

    for part in parts:
        await _run_tts(session, part, run, rate)
        await _run_render(session, part, run)

    log.info("all parts processed", run_id=str(run.id))


async def _run_tts(session, part: PipelinePart, run: PipelineRun, rate: str | None = None) -> None:
    log.info("generating audio", run_id=str(run.id), part=part.part_number, rate=rate)
    part.status = PartStatus.tts_running
    await session.commit()

    audio_key, srt_key = await TTSClient().generate(
        text=part.script,
        run_id=str(run.id),
        part_number=part.part_number,
        rate=rate,
    )

    part.audio_key = audio_key
    part.srt_key = srt_key
    part.status = PartStatus.tts_done
    await session.commit()
    log.info("audio ready", run_id=str(run.id), part=part.part_number, audio=audio_key, srt=srt_key)


async def _run_render(session, part: PipelinePart, run: PipelineRun) -> None:
    log.info("starting render", run_id=str(run.id), part=part.part_number)
    part.status = PartStatus.render_pending
    await session.commit()

    blender = BlenderClient()
    video_id = await blender.create_video(
        video_file_key=settings.CONFIG.template.background_video_key,
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
    log.info("render done", run_id=str(run.id), part=part.part_number, key=part.video_key)


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
            log.warning("part has no video_key, skipping schedule", part=part.part_number)
            continue
        data = await tiktok.schedule(
            video_key=part.video_key,
            classification=run.classification or {},
            part_number=part.part_number,
            series_id=str(run.id),
        )
        raw_ts = data.get("scheduled_at")
        part.scheduled_at = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")) if raw_ts else None
        part.tiktok_video_id = data.get("buffer_update_id")
        await session.commit()

    run.status = PipelineStatus.scheduled
    await session.commit()
    log.info("pipeline scheduled", run_id=str(run.id))
