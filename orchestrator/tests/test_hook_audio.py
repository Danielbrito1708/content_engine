"""Narração da frase gancho em arquivo próprio.

O gancho já é narrado dentro da parte 1 (é a primeira frase dela). Este áudio
existe separado para poder abrir o vídeo sozinho — por isso a etapa é
degradável: perder o extra não pode custar o vídeo.
"""
import json
import uuid
from unittest.mock import AsyncMock

import respx
from httpx import Response
from sqlalchemy import select

from src.orchestrator.db.models import PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.worker import _refine, _run_hook_tts, run_pipeline

HOOK = "Ela achou a mensagem às 3 da manhã."

CLASSIFICATION = {
    "content_type": "drama",
    "tone": "emocional",
    "target_audience": {"age_range": [18, 30], "gender": "F", "interests": []},
    "cta_per_part": ["Comenta!"],
    "hashtag_hints": [],
}


async def _make_run(session, hook=None):
    run = PipelineRun(raw_script="Roteiro de teste.", hook=hook)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


def _refine_response(**extra):
    return Response(200, json={"parts": ["Parte única."], "classification": CLASSIFICATION, **extra})


# ── _refine guarda o gancho ────────────────────────────────────────────────

@respx.mock
async def test_refine_stores_hook(session):
    run = await _make_run(session)
    respx.post("http://llm_service:8000/refine").mock(return_value=_refine_response(hook=HOOK))

    await _refine(session, run)

    await session.refresh(run)
    assert run.hook == HOOK


@respx.mock
async def test_refine_without_hook_leaves_it_null(session):
    """llm_service antigo não devolve o campo — o deploy dos dois não é atômico."""
    run = await _make_run(session)
    respx.post("http://llm_service:8000/refine").mock(return_value=_refine_response())

    await _refine(session, run)

    await session.refresh(run)
    assert run.hook is None
    assert run.status == PipelineStatus.refined


# ── _run_hook_tts ──────────────────────────────────────────────────────────

@respx.mock
async def test_hook_tts_stores_keys(session):
    run = await _make_run(session, hook=HOOK)
    route = respx.post("http://tts_service:8000/generate").mock(
        return_value=Response(200, json={
            "audio_key": f"audio/{run.id}/hook.mp3",
            "srt_key": f"subs/{run.id}/hook.srt",
        })
    )

    await _run_hook_tts(session, run)

    await session.refresh(run)
    assert run.hook_audio_key == f"audio/{run.id}/hook.mp3"
    assert run.hook_srt_key == f"subs/{run.id}/hook.srt"

    sent = json.loads(route.calls.last.request.content)
    assert sent["text"] == HOOK
    assert sent["label"] == "hook"


@respx.mock
async def test_hook_tts_is_skipped_without_hook(session):
    run = await _make_run(session, hook=None)
    route = respx.post("http://tts_service:8000/generate").mock(return_value=Response(200, json={}))

    await _run_hook_tts(session, run)

    await session.refresh(run)
    assert not route.called
    assert run.hook_audio_key is None


@respx.mock
async def test_hook_tts_failure_does_not_break_the_run(session):
    run = await _make_run(session, hook=HOOK)
    respx.post("http://tts_service:8000/generate").mock(return_value=Response(502))

    await _run_hook_tts(session, run)  # não levanta

    await session.refresh(run)
    assert run.hook_audio_key is None
    assert run.hook == HOOK


# ── pipeline completo ──────────────────────────────────────────────────────

@respx.mock
async def test_run_pipeline_generates_hook_audio(session, monkeypatch):
    run = await _make_run(session)
    video_id, job_id = uuid.uuid4(), uuid.uuid4()

    monkeypatch.setenv("BLENDER_TEMPLATE_ID", str(uuid.uuid4()))
    monkeypatch.setattr("src.orchestrator.worker.upload_bytes", AsyncMock())

    respx.post("http://llm_service:8000/refine").mock(return_value=_refine_response(hook=HOOK))

    # O gancho vai na mesma rota das partes; a key sai do `label`.
    def _tts(request):
        payload = json.loads(request.content)
        slug = payload.get("label") or f"part_{payload['part_number']}"
        return Response(200, json={
            "audio_key": f"audio/{run.id}/{slug}.mp3",
            "srt_key": f"subs/{run.id}/{slug}.srt",
        })

    respx.post("http://tts_service:8000/generate").mock(side_effect=_tts)
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
        return_value=Response(200, json={"scheduled_at": "2025-06-01T08:00:00Z", "buffer_update_id": "b1"})
    )

    await run_pipeline(run.id)

    await session.refresh(run)
    assert run.status == PipelineStatus.scheduled
    assert run.hook == HOOK
    assert run.hook_audio_key == f"audio/{run.id}/hook.mp3"

    # A parte continua com o arquivo dela — o gancho não sobrescreveu nada.
    result = await session.execute(select(PipelinePart).where(PipelinePart.run_id == run.id))
    assert result.scalars().first().audio_key == f"audio/{run.id}/part_1.mp3"


@respx.mock
async def test_hook_audio_failure_still_produces_the_video(session, monkeypatch):
    run = await _make_run(session)
    video_id, job_id = uuid.uuid4(), uuid.uuid4()

    monkeypatch.setenv("BLENDER_TEMPLATE_ID", str(uuid.uuid4()))
    monkeypatch.setattr("src.orchestrator.worker.upload_bytes", AsyncMock())

    respx.post("http://llm_service:8000/refine").mock(return_value=_refine_response(hook=HOOK))

    calls = {"n": 0}

    def _tts(request):
        calls["n"] += 1
        if calls["n"] == 1:  # o gancho é o primeiro TTS do run
            return Response(502)
        return Response(200, json={
            "audio_key": f"audio/{run.id}/part_1.mp3",
            "srt_key": f"subs/{run.id}/part_1.srt",
        })

    respx.post("http://tts_service:8000/generate").mock(side_effect=_tts)
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
        return_value=Response(200, json={"scheduled_at": "2025-06-01T08:00:00Z", "buffer_update_id": "b1"})
    )

    await run_pipeline(run.id)

    await session.refresh(run)
    assert run.status == PipelineStatus.scheduled
    assert run.hook_audio_key is None
