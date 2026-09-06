"""A trilha combina com o clima da história: história triste, música triste.

O refino classifica o mood; `_pick_music` escolhe a faixa sob `{prefix}{mood}/`
e guarda no run, para todas as partes usarem a mesma cama sonora — mesmo padrão
do `card_key`. Mood sem faixa cai na trilha default, sem aviso: é o estado
inicial esperado da biblioteca, não uma falha.
"""
import json
import uuid
from unittest.mock import AsyncMock

import pytest
import respx
from httpx import Response

from src.core import settings
from src.orchestrator.db.models import PipelineRun, PipelineStatus
from src.orchestrator.music import is_track, pick_music
from src.orchestrator.worker import _pick_music, _refine

REFINE_URL = "http://llm_service:8000/refine"

CLASSIFICATION = {
    "content_type": "drama",
    "tone": "emotional",
    "target_audience": {"age_range": [18, 30], "gender": "female", "interests": []},
    "cta_per_part": ["Comenta!"],
    "hashtag_hints": [],
}

DEFAULT_KEY = settings.CONFIG.music.default_key
PREFIX = settings.CONFIG.music.prefix


async def _make_run(session, **kwargs):
    run = PipelineRun(raw_script="Roteiro de teste.", **kwargs)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


def _refine_response(**extra):
    return Response(200, json={"parts": ["Parte única."], "classification": CLASSIFICATION, **extra})


# ── music.pick_music / is_track ───────────────────────────────────────────


def test_is_track_accepts_common_audio_extensions():
    assert is_track("assets/music/sad/piano.mp3")
    assert is_track("assets/music/sad/piano.WAV")
    assert not is_track("assets/music/sad/")
    assert not is_track("assets/music/sad/cover.jpg")


def test_pick_music_is_deterministic_for_the_same_run():
    keys = ["assets/music/sad/a.mp3", "assets/music/sad/b.mp3", "assets/music/sad/c.mp3"]
    run_id = str(uuid.uuid4())
    assert pick_music(keys, run_id) == pick_music(keys, run_id)


def test_pick_music_raises_on_empty_candidates():
    with pytest.raises(ValueError):
        pick_music([], str(uuid.uuid4()))


# ── _refine guarda o mood ──────────────────────────────────────────────────


@respx.mock
async def test_refine_stores_the_mood(session):
    run = await _make_run(session)
    respx.post(REFINE_URL).mock(return_value=_refine_response(mood="sad"))

    await _refine(session, run)

    await session.refresh(run)
    assert run.mood == "sad"


@respx.mock
async def test_refine_without_the_field_falls_back_to_neutral(session):
    """`llm_service` antigo não devolve o campo — o run cai na trilha default,
    a mesma que todo vídeo usava antes deste campo existir."""
    run = await _make_run(session)
    respx.post(REFINE_URL).mock(return_value=_refine_response())

    await _refine(session, run)

    await session.refresh(run)
    assert run.mood == "neutral"
    assert run.status == PipelineStatus.refined


# ── _pick_music ─────────────────────────────────────────────────────────


async def test_pick_music_uses_a_track_from_the_mood_folder(session, monkeypatch):
    run = await _make_run(session, mood="sad")
    track = f"{PREFIX}sad/piano-triste.mp3"
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[track]))

    await _pick_music(session, run)

    await session.refresh(run)
    assert run.music_key == track


async def test_pick_music_falls_back_to_default_when_mood_folder_is_empty(session, monkeypatch):
    """Estado inicial esperado: a biblioteca só tem a faixa neutra até alguém
    subir faixas para os outros moods. Não é degradação."""
    run = await _make_run(session, mood="sad")
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    await _pick_music(session, run)

    await session.refresh(run)
    assert run.music_key == DEFAULT_KEY


async def test_pick_music_ignores_non_audio_objects_under_the_mood_folder(session, monkeypatch):
    run = await _make_run(session, mood="hopeful")
    monkeypatch.setattr(
        "src.orchestrator.worker.list_keys",
        AsyncMock(return_value=[f"{PREFIX}hopeful/cover.jpg"]),
    )

    await _pick_music(session, run)

    await session.refresh(run)
    assert run.music_key == DEFAULT_KEY


async def test_pick_music_falls_back_to_default_and_warns_when_listing_fails(session, monkeypatch):
    """Ao contrário da pasta vazia, esta é a degradação de verdade — o bucket
    está fora do ar, não apenas sem faixa para o mood — e avisa."""
    run = await _make_run(session, mood="tense")
    monkeypatch.setattr(
        "src.orchestrator.worker.list_keys", AsyncMock(side_effect=RuntimeError("bucket unreachable"))
    )
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "http://ntfy.local/topic")

    from src.core import notify as notify_module
    notify_module.reset()

    await _pick_music(session, run)

    await session.refresh(run)
    assert run.music_key == DEFAULT_KEY
    queue = notify_module._get_queue()
    queued = [queue.get_nowait() for _ in range(queue.qsize())]
    assert any("música" in m for m in queued)


async def test_pick_music_is_deterministic_across_two_calls(session, monkeypatch):
    """Um run re-renderizado depois de um restart volta com a mesma trilha."""
    run = await _make_run(session, mood="sad")
    tracks = [f"{PREFIX}sad/a.mp3", f"{PREFIX}sad/b.mp3"]
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=tracks))

    await _pick_music(session, run)
    await session.refresh(run)
    first = run.music_key

    await _pick_music(session, run)
    await session.refresh(run)
    assert run.music_key == first


# ── o music_key chega ao blender_worker ───────────────────────────────────


@respx.mock
async def test_run_render_sends_the_picked_music_key(session, monkeypatch):
    from src.orchestrator.db.models import PipelinePart
    from src.orchestrator.worker import _run_render

    run = await _make_run(session, music_key="assets/music/sad/piano.mp3")
    part = PipelinePart(run_id=run.id, part_number=1, script="Parte 1.", audio_key="a.mp3", srt_key="a.srt")
    session.add(part)
    await session.commit()
    await session.refresh(part)

    monkeypatch.setenv("BLENDER_TEMPLATE_ID", str(uuid.uuid4()))
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    video_id, job_id = uuid.uuid4(), uuid.uuid4()
    videos_route = respx.post("http://blender_worker:8000/videos").mock(
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

    await _run_render(session, part, run)

    sent = json.loads(videos_route.calls.last.request.content)
    assert sent["music_key"] == "assets/music/sad/piano.mp3"


# ── API ───────────────────────────────────────────────────────────────────


async def test_pipeline_response_exposes_mood_and_music_key(client, session):
    run = await _make_run(session, mood="sad", music_key="assets/music/sad/piano.mp3")

    resp = await client.get(f"/pipeline/{run.id}")

    assert resp.status_code == 200
    assert resp.json()["mood"] == "sad"
    assert resp.json()["music_key"] == "assets/music/sad/piano.mp3"
