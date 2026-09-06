import json
import uuid
from unittest.mock import AsyncMock

import respx
from httpx import Response

from src.core import settings
from src.orchestrator.db.models import PipelinePart, PipelineRun
from src.orchestrator.worker import _run_render, _template_id_for

DEFAULT_TEMPLATE_ID = uuid.UUID(settings.env.blender_template_id)


async def _make_run(session, template_id=None):
    run = PipelineRun(raw_script="Roteiro.", template_id=template_id)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _make_part(session, run_id, audio_key="audio/x/part_1.mp3"):
    part = PipelinePart(run_id=run_id, part_number=1, script="Parte 1.", audio_key=audio_key)
    session.add(part)
    await session.commit()
    await session.refresh(part)
    return part


# ── PipelineCreate / PipelineResponse (schema) ──────────────────────────────

async def test_create_pipeline_without_template_id_defaults_to_none(client, mock_run_pipeline):
    resp = await client.post("/pipeline", json={"script": "Script."})
    assert resp.status_code == 201
    assert resp.json()["template_id"] is None


async def test_create_pipeline_persists_template_id(client, mock_run_pipeline):
    template_id = str(uuid.uuid4())
    resp = await client.post("/pipeline", json={"script": "Script.", "template_id": template_id})
    assert resp.status_code == 201
    assert resp.json()["template_id"] == template_id


async def test_get_pipeline_exposes_template_id(client, mock_run_pipeline):
    template_id = str(uuid.uuid4())
    create = await client.post("/pipeline", json={"script": "Script.", "template_id": template_id})
    run_id = create.json()["id"]

    got = await client.get(f"/pipeline/{run_id}")
    assert got.json()["template_id"] == template_id


async def test_create_pipeline_rejects_invalid_template_id(client, mock_run_pipeline):
    resp = await client.post("/pipeline", json={"script": "Script.", "template_id": "not-a-uuid"})
    assert resp.status_code == 422


# ── _template_id_for (pure resolution) ──────────────────────────────────────

async def test_template_id_for_falls_back_to_default(session):
    run = await _make_run(session)
    assert _template_id_for(run) == DEFAULT_TEMPLATE_ID


async def test_template_id_for_uses_run_override(session):
    override = uuid.uuid4()
    run = await _make_run(session, template_id=override)
    assert _template_id_for(run) == override


# ── _run_render sends the resolved template_id to POST /jobs ───────────────

@respx.mock
async def test_run_render_sends_default_template_id(session, monkeypatch):
    run = await _make_run(session)
    part = await _make_part(session, run.id)
    video_id = uuid.uuid4()
    job_id = uuid.uuid4()
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    respx.post("http://blender_worker:8000/videos").mock(
        return_value=Response(201, json={
            "id": str(video_id), "video_file_key": "assets/bg.mp4", "music_key": "assets/music.mp3",
            "voice_key": "audio/x.mp3", "subtitle_key": "subs/x.srt", "video_metadata": None,
            "created_at": "2025-01-01T00:00:00Z",
        })
    )
    jobs_route = respx.post("http://blender_worker:8000/jobs").mock(
        return_value=Response(201, json={
            "id": str(job_id), "video_id": str(video_id), "template_id": str(DEFAULT_TEMPLATE_ID),
            "status": "pending", "output_key": None, "params": None, "error": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        })
    )
    respx.get(f"http://blender_worker:8000/jobs/{job_id}").mock(
        return_value=Response(200, json={
            "id": str(job_id), "video_id": str(video_id), "template_id": str(DEFAULT_TEMPLATE_ID),
            "status": "completed", "output_key": f"outputs/{job_id}.mp4", "params": None, "error": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        })
    )

    await _run_render(session, part, run)

    sent = json.loads(jobs_route.calls.last.request.content)
    assert sent["template_id"] == str(DEFAULT_TEMPLATE_ID)


@respx.mock
async def test_run_render_sends_run_override_template_id(session, monkeypatch):
    override = uuid.uuid4()
    run = await _make_run(session, template_id=override)
    part = await _make_part(session, run.id)
    video_id = uuid.uuid4()
    job_id = uuid.uuid4()
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    respx.post("http://blender_worker:8000/videos").mock(
        return_value=Response(201, json={
            "id": str(video_id), "video_file_key": "assets/bg.mp4", "music_key": "assets/music.mp3",
            "voice_key": "audio/x.mp3", "subtitle_key": "subs/x.srt", "video_metadata": None,
            "created_at": "2025-01-01T00:00:00Z",
        })
    )
    jobs_route = respx.post("http://blender_worker:8000/jobs").mock(
        return_value=Response(201, json={
            "id": str(job_id), "video_id": str(video_id), "template_id": str(override),
            "status": "pending", "output_key": None, "params": None, "error": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        })
    )
    respx.get(f"http://blender_worker:8000/jobs/{job_id}").mock(
        return_value=Response(200, json={
            "id": str(job_id), "video_id": str(video_id), "template_id": str(override),
            "status": "completed", "output_key": f"outputs/{job_id}.mp4", "params": None, "error": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        })
    )

    await _run_render(session, part, run)

    sent = json.loads(jobs_route.calls.last.request.content)
    assert sent["template_id"] == str(override)


# ── template_id chega ao tiktok_poster, para virar variante de teste A/B ────

@respx.mock
async def test_scheduled_part_sends_the_resolved_template_id_to_the_poster(session):
    override = uuid.uuid4()
    run = await _make_run(session, template_id=override)
    part = await _make_part(session, run.id)
    part.video_key = "outputs/x.mp4"
    await session.commit()

    route = respx.post("http://tiktok_poster:8000/schedule").mock(
        return_value=Response(200, json={"scheduled_at": "2025-06-01T08:00:00Z", "buffer_update_id": "b1"})
    )

    from src.orchestrator.worker import _schedule
    await _schedule(session, run)

    sent = json.loads(route.calls.last.request.content)
    assert sent["template_id"] == str(override)


@respx.mock
async def test_scheduled_part_sends_the_default_template_id_when_run_has_no_override(session):
    run = await _make_run(session)
    part = await _make_part(session, run.id)
    part.video_key = "outputs/x.mp4"
    await session.commit()

    route = respx.post("http://tiktok_poster:8000/schedule").mock(
        return_value=Response(200, json={"scheduled_at": "2025-06-01T08:00:00Z", "buffer_update_id": "b1"})
    )

    from src.orchestrator.worker import _schedule
    await _schedule(session, run)

    sent = json.loads(route.calls.last.request.content)
    assert sent["template_id"] == str(DEFAULT_TEMPLATE_ID)
