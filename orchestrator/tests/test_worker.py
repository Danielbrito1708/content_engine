import uuid
from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response

from src.orchestrator.db.models import PartStatus, PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.worker import _refine, _run_render, _run_tts, _schedule, run_pipeline


# ── helpers ────────────────────────────────────────────────────────────────

async def _make_run(session, script="Roteiro de teste.", metadata=None):
    run = PipelineRun(raw_script=script, input_metadata=metadata)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _make_part(session, run_id, number=1, script="Parte 1.", audio_key=None, video_key=None):
    part = PipelinePart(
        run_id=run_id,
        part_number=number,
        script=script,
        audio_key=audio_key,
        video_key=video_key,
    )
    session.add(part)
    await session.commit()
    await session.refresh(part)
    return part


# ── _refine ────────────────────────────────────────────────────────────────

@respx.mock
async def test_refine_creates_parts(session):
    run = await _make_run(session)

    respx.post("http://llm_service:8000/refine").mock(
        return_value=Response(
            200,
            json={
                "parts": ["Parte 1 do roteiro.", "Parte 2 do roteiro."],
                "classification": {
                    "content_type": "drama",
                    "tone": "emocional",
                    "target_audience": {"age_range": [18, 30], "gender": "F", "interests": ["relacionamentos"]},
                    "cta_per_part": ["Segue pra parte 2!", "Comenta o que achou!"],
                    "hashtag_hints": ["#drama", "#relacionamentos"],
                },
            },
        )
    )

    await _refine(session, run)

    await session.refresh(run)
    assert run.status == PipelineStatus.refined
    assert run.parts_count == 2
    assert run.classification["content_type"] == "drama"

    from sqlalchemy import select
    result = await session.execute(select(PipelinePart).where(PipelinePart.run_id == run.id))
    parts = result.scalars().all()
    assert len(parts) == 2
    assert parts[0].script == "Parte 1 do roteiro."


@respx.mock
async def test_refine_marks_failed_on_http_error(session):
    run = await _make_run(session)

    respx.post("http://llm_service:8000/refine").mock(return_value=Response(500))

    with pytest.raises(Exception):
        await _refine(session, run)


# ── _run_tts ───────────────────────────────────────────────────────────────

@respx.mock
async def test_run_tts_sets_audio_and_srt_keys(session):
    run = await _make_run(session)
    part = await _make_part(session, run.id)

    # tts_service transcribes its own audio and returns both keys — the
    # orchestrator never generates the SRT itself.
    respx.post("http://tts_service:8000/generate").mock(
        return_value=Response(200, json={
            "audio_key": f"audio/{run.id}/part_1.mp3",
            "srt_key": f"subs/{run.id}/part_1.srt",
        })
    )

    await _run_tts(session, part, run)

    await session.refresh(part)
    assert part.status == PartStatus.tts_done
    assert part.audio_key == f"audio/{run.id}/part_1.mp3"
    assert part.srt_key == f"subs/{run.id}/part_1.srt"


@respx.mock
async def test_run_tts_raises_on_failure(session):
    run = await _make_run(session)
    part = await _make_part(session, run.id)

    respx.post("http://tts_service:8000/generate").mock(return_value=Response(502))

    with pytest.raises(Exception):
        await _run_tts(session, part, run)


# ── _run_render ────────────────────────────────────────────────────────────

@respx.mock
async def test_run_render_full_flow(session, monkeypatch):
    run = await _make_run(session)
    video_id = uuid.uuid4()
    job_id = uuid.uuid4()
    part = await _make_part(session, run.id, audio_key=f"audio/{run.id}/part_1.mp3")

    monkeypatch.setenv("BLENDER_TEMPLATE_ID", str(uuid.uuid4()))
    # No upload to mock: the worker stopped generating SRTs — tts_service
    # publishes them and hands back the key.
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    respx.post("http://blender_worker:8000/videos").mock(
        return_value=Response(201, json={"id": str(video_id), "video_file_key": "assets/bg.mp4",
                                         "music_key": "assets/music.mp3", "voice_key": "audio/x.mp3",
                                         "subtitle_key": "subs/x.srt", "video_metadata": None,
                                         "created_at": "2025-01-01T00:00:00Z"})
    )
    respx.post("http://blender_worker:8000/jobs").mock(
        return_value=Response(201, json={
            "id": str(job_id), "video_id": str(video_id),
            "template_id": str(uuid.uuid4()), "status": "pending",
            "output_key": None, "params": None, "error": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        })
    )
    respx.get(f"http://blender_worker:8000/jobs/{job_id}").mock(
        return_value=Response(200, json={
            "id": str(job_id), "video_id": str(video_id),
            "template_id": str(uuid.uuid4()), "status": "completed",
            "output_key": f"outputs/{job_id}.mp4", "params": None, "error": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        })
    )

    await _run_render(session, part, run)

    await session.refresh(part)
    assert part.status == PartStatus.render_done
    assert part.video_key == f"outputs/{job_id}.mp4"
    assert part.blender_job_id == job_id


@respx.mock
async def test_run_render_raises_on_job_failure(session, monkeypatch):
    run = await _make_run(session)
    video_id = uuid.uuid4()
    job_id = uuid.uuid4()
    part = await _make_part(session, run.id, audio_key="audio/x.mp3")

    monkeypatch.setenv("BLENDER_TEMPLATE_ID", str(uuid.uuid4()))
    # No upload to mock: the worker stopped generating SRTs — tts_service
    # publishes them and hands back the key.
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    respx.post("http://blender_worker:8000/videos").mock(
        return_value=Response(201, json={"id": str(video_id), "video_file_key": "x", "music_key": "x",
                                         "voice_key": "x", "subtitle_key": "x", "video_metadata": None,
                                         "created_at": "2025-01-01T00:00:00Z"})
    )
    respx.post("http://blender_worker:8000/jobs").mock(
        return_value=Response(201, json={
            "id": str(job_id), "video_id": str(video_id), "template_id": str(uuid.uuid4()),
            "status": "pending", "output_key": None, "params": None, "error": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        })
    )
    respx.get(f"http://blender_worker:8000/jobs/{job_id}").mock(
        return_value=Response(200, json={
            "id": str(job_id), "video_id": str(video_id), "template_id": str(uuid.uuid4()),
            "status": "failed", "output_key": None, "params": None, "error": "blender crash",
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        })
    )

    with pytest.raises(RuntimeError, match="render job failed"):
        await _run_render(session, part, run)


# ── _schedule ──────────────────────────────────────────────────────────────

@respx.mock
async def test_schedule_sets_scheduled_at(session):
    run = await _make_run(session)
    run.classification = {"content_type": "drama", "tone": "emocional",
                          "target_audience": {"age_range": [18, 30], "gender": "F", "interests": []},
                          "cta_per_part": ["Segue!"], "hashtag_hints": []}
    await session.commit()

    part = await _make_part(session, run.id, video_key="outputs/abc.mp4")

    respx.post("http://tiktok_poster:8000/schedule").mock(
        return_value=Response(200, json={
            "scheduled_at": "2025-06-01T08:00:00Z",
            "buffer_update_id": "buf_123",
        })
    )

    await _schedule(session, run)

    await session.refresh(run)
    await session.refresh(part)
    assert run.status == PipelineStatus.scheduled
    assert part.scheduled_at is not None
    assert part.tiktok_video_id == "buf_123"


@respx.mock
async def test_schedule_skips_parts_without_video(session):
    run = await _make_run(session)
    run.classification = {}
    await session.commit()

    part = await _make_part(session, run.id, video_key=None)

    await _schedule(session, run)

    await session.refresh(run)
    assert run.status == PipelineStatus.scheduled
    assert part.scheduled_at is None


# ── run_pipeline (end-to-end with all mocks) ───────────────────────────────

@respx.mock
async def test_run_pipeline_full(session, monkeypatch):
    run = await _make_run(session)
    video_id = uuid.uuid4()
    job_id = uuid.uuid4()

    monkeypatch.setenv("BLENDER_TEMPLATE_ID", str(uuid.uuid4()))
    # No upload to mock: the worker stopped generating SRTs — tts_service
    # publishes them and hands back the key.
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    respx.post("http://llm_service:8000/refine").mock(
        return_value=Response(200, json={
            "parts": ["Parte única."],
            "classification": {
                "content_type": "drama", "tone": "serio",
                "target_audience": {"age_range": [18, 35], "gender": "F", "interests": []},
                "cta_per_part": ["Comenta!"], "hashtag_hints": [],
            },
        })
    )
    respx.post("http://tts_service:8000/generate").mock(
        return_value=Response(200, json={
            "audio_key": f"audio/{run.id}/part_1.mp3",
            "srt_key": f"subs/{run.id}/part_1.srt",
        })
    )
    respx.post("http://blender_worker:8000/videos").mock(
        return_value=Response(201, json={"id": str(video_id), "video_file_key": "x", "music_key": "x",
                                         "voice_key": "x", "subtitle_key": "x", "video_metadata": None,
                                         "created_at": "2025-01-01T00:00:00Z"})
    )
    respx.post("http://blender_worker:8000/jobs").mock(
        return_value=Response(201, json={
            "id": str(job_id), "video_id": str(video_id), "template_id": str(uuid.uuid4()),
            "status": "pending", "output_key": None, "params": None, "error": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        })
    )
    respx.get(f"http://blender_worker:8000/jobs/{job_id}").mock(
        return_value=Response(200, json={
            "id": str(job_id), "video_id": str(video_id), "template_id": str(uuid.uuid4()),
            "status": "completed", "output_key": f"outputs/{job_id}.mp4", "params": None, "error": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        })
    )
    respx.post("http://tiktok_poster:8000/schedule").mock(
        return_value=Response(200, json={
            "scheduled_at": "2025-06-01T08:00:00Z",
            "buffer_update_id": "buf_xyz",
        })
    )

    await run_pipeline(run.id)

    await session.refresh(run)
    assert run.status == PipelineStatus.scheduled


@respx.mock
async def test_run_pipeline_marks_failed_on_llm_error(session, monkeypatch):
    run = await _make_run(session)

    respx.post("http://llm_service:8000/refine").mock(return_value=Response(500))

    await run_pipeline(run.id)

    await session.refresh(run)
    assert run.status == PipelineStatus.failed
    assert run.error is not None
