"""O gênero do narrador atravessa o pipeline até virar voz.

O refino descobre quem conta a história; o orchestrador guarda no run e manda o
gênero — nunca o nome de uma voz — em toda chamada ao `tts_service`. Gancho e
partes têm de sair com o mesmo valor: é a mesma pessoa narrando.
"""
import json
import uuid
from unittest.mock import AsyncMock

import respx
from httpx import Response

from src.orchestrator.db.models import PartStatus, PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.worker import _refine, _run_hook_tts, _run_tts, run_pipeline

HOOK = "Eu descobri tudo num domingo de manhã."
TTS_URL = "http://tts_service:8000/generate"
REFINE_URL = "http://llm_service:8000/refine"

CLASSIFICATION = {
    "content_type": "drama",
    "tone": "emocional",
    # De propósito diferente do narrador nos testes abaixo: público-alvo e quem
    # narra são duas perguntas distintas, e confundi-las é o erro que este
    # campo existe para não cometer.
    "target_audience": {"age_range": [18, 30], "gender": "female", "interests": []},
    "cta_per_part": ["Comenta!"],
    "hashtag_hints": [],
}


async def _make_run(session, **kwargs):
    run = PipelineRun(raw_script="Roteiro de teste.", **kwargs)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _make_part(session, run_id):
    part = PipelinePart(run_id=run_id, part_number=1, script="Parte 1.")
    session.add(part)
    await session.commit()
    await session.refresh(part)
    return part


def _refine_response(**extra):
    return Response(200, json={"parts": ["Parte única."], "classification": CLASSIFICATION, **extra})


def _tts_ok(run_id, slug="part_1", voice="pt-BR-ThalitaNeural"):
    return Response(200, json={
        "audio_key": f"audio/{run_id}/{slug}.mp3",
        "srt_key": f"subs/{run_id}/{slug}.srt",
        "voice": voice,
    })


# ── _refine guarda o gênero ───────────────────────────────────────────────

@respx.mock
async def test_refine_stores_the_narrator_gender(session):
    run = await _make_run(session)
    respx.post(REFINE_URL).mock(return_value=_refine_response(narrator_gender="male"))

    await _refine(session, run)

    await session.refresh(run)
    assert run.narrator_gender == "male"


@respx.mock
async def test_refine_without_the_field_falls_back_to_unknown(session):
    """`llm_service` antigo não devolve o campo — o deploy dos dois não é
    atômico, e um run sem narrador identificado narra na voz padrão."""
    run = await _make_run(session)
    respx.post(REFINE_URL).mock(return_value=_refine_response())

    await _refine(session, run)

    await session.refresh(run)
    assert run.narrator_gender == "unknown"
    assert run.status == PipelineStatus.refined


@respx.mock
async def test_refine_normalizes_what_the_llm_returned(session):
    run = await _make_run(session)
    respx.post(REFINE_URL).mock(return_value=_refine_response(narrator_gender="Female"))

    await _refine(session, run)

    await session.refresh(run)
    assert run.narrator_gender == "female"


@respx.mock
async def test_narrator_gender_is_not_the_target_audience(session):
    """A classificação diz público feminino; quem narra é homem. O vídeo tem de
    sair na voz do narrador."""
    run = await _make_run(session)
    respx.post(REFINE_URL).mock(return_value=_refine_response(narrator_gender="male"))

    await _refine(session, run)

    await session.refresh(run)
    assert run.narrator_gender == "male"
    assert run.classification["target_audience"]["gender"] == "female"


# ── o gênero chega ao tts_service ─────────────────────────────────────────

@respx.mock
async def test_run_tts_sends_the_narrator_gender(session):
    run = await _make_run(session, narrator_gender="male")
    part = await _make_part(session, run.id)
    route = respx.post(TTS_URL).mock(return_value=_tts_ok(run.id))

    await _run_tts(session, part, run)

    assert json.loads(route.calls.last.request.content)["narrator_gender"] == "male"


@respx.mock
async def test_hook_tts_sends_the_narrator_gender(session):
    run = await _make_run(session, hook=HOOK, narrator_gender="female")
    route = respx.post(TTS_URL).mock(return_value=_tts_ok(run.id, "hook"))

    await _run_hook_tts(session, run)

    sent = json.loads(route.calls.last.request.content)
    assert sent["narrator_gender"] == "female"
    assert sent["label"] == "hook"


@respx.mock
async def test_run_without_a_gender_omits_the_field(session):
    """Omitido, não nulo: o `tts_service` aplica sua voz padrão, que é o que todo
    vídeo usava antes disso existir."""
    run = await _make_run(session)
    part = await _make_part(session, run.id)
    route = respx.post(TTS_URL).mock(return_value=_tts_ok(run.id))

    await _run_tts(session, part, run)

    assert "narrator_gender" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_the_orchestrator_never_sends_a_voice_name(session):
    """Que voz corresponde a que gênero é decisão do `tts_service`, que conhece
    os providers. Daqui sai um fato sobre o roteiro."""
    run = await _make_run(session, narrator_gender="male")
    part = await _make_part(session, run.id)
    route = respx.post(TTS_URL).mock(return_value=_tts_ok(run.id))

    await _run_tts(session, part, run)

    sent = json.loads(route.calls.last.request.content)
    assert "voice" not in sent
    assert "Neural" not in json.dumps(sent)


# ── pipeline completo ─────────────────────────────────────────────────────

@respx.mock
async def test_hook_and_parts_are_narrated_by_the_same_person(session, monkeypatch):
    """Gancho numa voz e narração em outra abre o vídeo com duas pessoas."""
    run = await _make_run(session)
    video_id, job_id = uuid.uuid4(), uuid.uuid4()

    monkeypatch.setenv("BLENDER_TEMPLATE_ID", str(uuid.uuid4()))
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    respx.post(REFINE_URL).mock(
        return_value=_refine_response(hook=HOOK, narrator_gender="male")
    )

    def _tts(request):
        payload = json.loads(request.content)
        slug = payload.get("label") or f"part_{payload['part_number']}"
        return _tts_ok(run.id, slug)

    tts_route = respx.post(TTS_URL).mock(side_effect=_tts)
    respx.post("http://blender_worker:8000/images/render").mock(
        return_value=Response(200, json={"output_key": f"cards/{run.id}.png"})
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
        return_value=Response(200, json={"scheduled_at": "2025-06-01T08:00:00Z", "buffer_update_id": "b1"})
    )

    await run_pipeline(run.id)

    await session.refresh(run)
    assert run.status == PipelineStatus.scheduled
    assert run.narrator_gender == "male"

    genders = [json.loads(c.request.content).get("narrator_gender") for c in tts_route.calls]
    assert len(genders) >= 2  # gancho + parte
    assert set(genders) == {"male"}


# ── a voz resolvida pelo tts_service é guardada no run ────────────────────

@respx.mock
async def test_run_tts_stores_the_resolved_voice(session):
    run = await _make_run(session, narrator_gender="male")
    part = await _make_part(session, run.id)
    respx.post(TTS_URL).mock(return_value=_tts_ok(run.id, voice="pt-BR-AntonioNeural"))

    await _run_tts(session, part, run)

    await session.refresh(run)
    assert run.tts_voice == "pt-BR-AntonioNeural"


@respx.mock
async def test_hook_tts_stores_the_resolved_voice(session):
    run = await _make_run(session, hook=HOOK, narrator_gender="female")
    respx.post(TTS_URL).mock(return_value=_tts_ok(run.id, "hook", voice="pt-BR-FranciscaNeural"))

    await _run_hook_tts(session, run)

    await session.refresh(run)
    assert run.tts_voice == "pt-BR-FranciscaNeural"


@respx.mock
async def test_voice_is_not_overwritten_by_a_later_part(session):
    """Uma vez capturada, a voz não muda no meio do run — mesma pessoa
    narrando a série inteira."""
    run = await _make_run(session, narrator_gender="male", tts_voice="pt-BR-AntonioNeural")
    part = await _make_part(session, run.id)
    respx.post(TTS_URL).mock(return_value=_tts_ok(run.id, voice="outra-voz-qualquer"))

    await _run_tts(session, part, run)

    await session.refresh(run)
    assert run.tts_voice == "pt-BR-AntonioNeural"


@respx.mock
async def test_missing_voice_field_does_not_crash_an_old_tts_service(session):
    """Um `tts_service` mais velho que este cliente não manda `voice` — o
    campo é cosmético, e perdê-lo não pode custar o run."""
    run = await _make_run(session)
    part = await _make_part(session, run.id)
    respx.post(TTS_URL).mock(return_value=Response(200, json={
        "audio_key": f"audio/{run.id}/part_1.mp3", "srt_key": f"subs/{run.id}/part_1.srt",
    }))

    await _run_tts(session, part, run)

    await session.refresh(run)
    assert run.tts_voice is None
    assert part.status == PartStatus.tts_done


# ── a voz chega ao tiktok_poster ───────────────────────────────────────────

@respx.mock
async def test_scheduled_part_sends_the_tts_voice_to_the_poster(session):
    run = await _make_run(session, tts_voice="pt-BR-AntonioNeural")
    part = await _make_part(session, run.id)
    part.video_key = "outputs/x.mp4"
    await session.commit()

    route = respx.post("http://tiktok_poster:8000/schedule").mock(
        return_value=Response(200, json={"scheduled_at": "2025-06-01T08:00:00Z", "buffer_update_id": "b1"})
    )

    from src.orchestrator.worker import _schedule
    await _schedule(session, run)

    sent = json.loads(route.calls.last.request.content)
    assert sent["tts_voice"] == "pt-BR-AntonioNeural"


@respx.mock
async def test_run_without_a_voice_omits_the_field(session):
    run = await _make_run(session)
    part = await _make_part(session, run.id)
    part.video_key = "outputs/x.mp4"
    await session.commit()

    route = respx.post("http://tiktok_poster:8000/schedule").mock(
        return_value=Response(200, json={"scheduled_at": "2025-06-01T08:00:00Z", "buffer_update_id": "b1"})
    )

    from src.orchestrator.worker import _schedule
    await _schedule(session, run)

    assert "tts_voice" not in json.loads(route.calls.last.request.content)


# ── API ───────────────────────────────────────────────────────────────────

async def test_pipeline_response_exposes_the_narrator_gender(client, session):
    run = await _make_run(session, narrator_gender="female")

    resp = await client.get(f"/pipeline/{run.id}")

    assert resp.status_code == 200
    assert resp.json()["narrator_gender"] == "female"


async def test_pipeline_response_exposes_the_tts_voice(client, session):
    run = await _make_run(session, tts_voice="pt-BR-AntonioNeural")

    resp = await client.get(f"/pipeline/{run.id}")

    assert resp.status_code == 200
    assert resp.json()["tts_voice"] == "pt-BR-AntonioNeural"
