"""Intro do vídeo: card de comentário + áudio do gancho.

O card é composto uma vez por run, com a frase gancho, e vai junto do
`hook_audio_key` para o render de **todas** as partes. As duas peças são
degradáveis e independentes — o vídeo sai sem elas se qualquer uma falhar.
"""
import json
import uuid
from unittest.mock import AsyncMock

import respx
from httpx import Response

from src.orchestrator.db.models import PipelinePart, PipelineRun
from src.orchestrator.worker import _render_card, _run_hook_tts, _run_render, _run_tts

HOOK = "Ela achou a mensagem às 3 da manhã."
CARD_URL = "http://blender_worker:8000/images/render"


async def _make_run(session, hook=HOOK, **fields):
    run = PipelineRun(raw_script="Roteiro de teste.", hook=hook, **fields)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _make_part(session, run_id, script="Parte 1.", **fields):
    part = PipelinePart(run_id=run_id, part_number=1, script=script, **fields)
    session.add(part)
    await session.commit()
    await session.refresh(part)
    return part


# ── _render_card ───────────────────────────────────────────────────────────

@respx.mock
async def test_card_is_composed_with_the_hook_text(session):
    run = await _make_run(session)
    route = respx.post(CARD_URL).mock(
        return_value=Response(200, json={"output_key": f"cards/{run.id}.png"})
    )

    await _render_card(session, run)

    await session.refresh(run)
    assert run.card_key == f"cards/{run.id}.png"

    sent = json.loads(route.calls.last.request.content)
    assert sent["text"] == HOOK
    assert sent["template"] == "comment_default"
    assert sent["output_key"] == f"cards/{run.id}.png"


@respx.mock
async def test_card_key_comes_from_the_response_not_from_the_request(session):
    """Quem decide onde o PNG ficou é quem escreveu — o worker só pede um lugar."""
    run = await _make_run(session)
    respx.post(CARD_URL).mock(return_value=Response(200, json={"output_key": "renders/outro.png"}))

    await _render_card(session, run)

    await session.refresh(run)
    assert run.card_key == "renders/outro.png"


@respx.mock
async def test_card_is_skipped_without_hook(session):
    run = await _make_run(session, hook=None)
    route = respx.post(CARD_URL).mock(return_value=Response(200, json={"output_key": "x.png"}))

    await _render_card(session, run)

    await session.refresh(run)
    assert not route.called
    assert run.card_key is None


@respx.mock
async def test_card_failure_does_not_break_the_run(session):
    run = await _make_run(session)
    respx.post(CARD_URL).mock(return_value=Response(500))

    await _render_card(session, run)  # não levanta

    await session.refresh(run)
    assert run.card_key is None
    assert run.hook == HOOK


# ── o gancho vai no mesmo rate da narração ─────────────────────────────────

@respx.mock
async def test_hook_tts_is_narrated_at_the_narration_rate(session):
    """O gancho agora abre o vídeo: velocidade diferente soa como outra voz."""
    run = await _make_run(session)
    route = respx.post("http://tts_service:8000/generate").mock(
        return_value=Response(200, json={
            "audio_key": f"audio/{run.id}/hook.mp3",
            "srt_key": f"subs/{run.id}/hook.srt",
        })
    )

    await _run_hook_tts(session, run, "+15%")

    sent = json.loads(route.calls.last.request.content)
    assert sent["rate"] == "+15%"
    assert sent["label"] == "hook"


# ── o gancho não é narrado duas vezes ──────────────────────────────────────

TTS_URL = "http://tts_service:8000/generate"
REST = "O celular estava na mesa, desbloqueado."


def _tts_ok(run_id, part=1):
    return Response(200, json={
        "audio_key": f"audio/{run_id}/part_{part}.mp3",
        "srt_key": f"subs/{run_id}/part_{part}.srt",
    })


@respx.mock
async def test_part_one_is_narrated_without_the_hook(session):
    """O gancho abre o vídeo sobre o card; repeti-lo na narração é dizer duas vezes."""
    run = await _make_run(session, hook_audio_key="audio/abc/hook.mp3")
    part = await _make_part(session, run.id, script=f"{HOOK} {REST}")

    route = respx.post(TTS_URL).mock(return_value=_tts_ok(run.id))

    await _run_tts(session, part, run)

    assert json.loads(route.calls.last.request.content)["text"] == REST


@respx.mock
async def test_part_two_keeps_its_whole_script(session):
    """Só a parte 1 abre com o gancho — nas outras não há o que cortar."""
    run = await _make_run(session, hook_audio_key="audio/abc/hook.mp3")
    part_two = PipelinePart(run_id=run.id, part_number=2, script=f"{HOOK} {REST}")
    session.add(part_two)
    await session.commit()
    await session.refresh(part_two)

    route = respx.post(TTS_URL).mock(return_value=_tts_ok(run.id, part=2))

    await _run_tts(session, part_two, run)

    assert json.loads(route.calls.last.request.content)["text"] == f"{HOOK} {REST}"


@respx.mock
async def test_hook_stays_in_the_narration_when_there_is_no_hook_audio(session):
    """Sem áudio do gancho não há abertura narrada: cortar apagaria a frase do vídeo."""
    run = await _make_run(session, hook_audio_key=None)
    part = await _make_part(session, run.id, script=f"{HOOK} {REST}")

    route = respx.post(TTS_URL).mock(return_value=_tts_ok(run.id))

    await _run_tts(session, part, run)

    assert json.loads(route.calls.last.request.content)["text"] == f"{HOOK} {REST}"


@respx.mock
async def test_script_that_does_not_open_with_the_hook_is_sent_whole(session):
    """Modelo que não copiou a frase literalmente: repetir é melhor que cortar errado."""
    run = await _make_run(session, hook_audio_key="audio/abc/hook.mp3")
    part = await _make_part(session, run.id, script=f"{REST} {HOOK}")

    route = respx.post(TTS_URL).mock(return_value=_tts_ok(run.id))

    await _run_tts(session, part, run)

    assert json.loads(route.calls.last.request.content)["text"] == f"{REST} {HOOK}"


# ── as duas keys chegam ao render ──────────────────────────────────────────

def _blender_mocks(video_id, job_id):
    respx.post("http://blender_worker:8000/videos").mock(
        return_value=Response(201, json={
            "id": str(video_id), "video_file_key": "x", "music_key": "x", "voice_key": "x",
            "subtitle_key": "x", "card_key": None, "hook_voice_key": None,
            "video_metadata": None, "created_at": "2025-01-01T00:00:00Z",
        })
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
            "status": "completed", "output_key": f"outputs/{job_id}.mp4", "params": None,
            "error": None, "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        })
    )


@respx.mock
async def test_render_receives_card_and_hook_audio(session, monkeypatch):
    run = await _make_run(
        session,
        card_key="cards/abc.png",
        hook_audio_key="audio/abc/hook.mp3",
    )
    part = await _make_part(session, run.id, audio_key="audio/abc/part_1.mp3",
                            srt_key="subs/abc/part_1.srt")

    monkeypatch.setenv("BLENDER_TEMPLATE_ID", str(uuid.uuid4()))
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    video_id, job_id = uuid.uuid4(), uuid.uuid4()
    _blender_mocks(video_id, job_id)

    await _run_render(session, part, run)

    sent = json.loads(respx.calls[0].request.content)
    assert sent["card_key"] == "cards/abc.png"
    assert sent["hook_voice_key"] == "audio/abc/hook.mp3"


@respx.mock
async def test_render_sends_null_keys_when_there_is_no_intro(session, monkeypatch):
    """Sem intro o vídeo ainda renderiza — as keys vão nulas, não ausentes."""
    run = await _make_run(session, hook=None)
    part = await _make_part(session, run.id, audio_key="audio/abc/part_1.mp3",
                            srt_key="subs/abc/part_1.srt")

    monkeypatch.setenv("BLENDER_TEMPLATE_ID", str(uuid.uuid4()))
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    video_id, job_id = uuid.uuid4(), uuid.uuid4()
    _blender_mocks(video_id, job_id)

    await _run_render(session, part, run)

    sent = json.loads(respx.calls[0].request.content)
    assert sent["card_key"] is None
    assert sent["hook_voice_key"] is None


@respx.mock
async def test_every_part_opens_with_the_same_intro(session, monkeypatch):
    """A intro é da série, não da parte 1 — é ela que dá a mesma cara a todas."""
    run = await _make_run(session, card_key="cards/abc.png", hook_audio_key="audio/abc/hook.mp3")
    part_two = PipelinePart(run_id=run.id, part_number=2, script="Parte 2.",
                            audio_key="audio/abc/part_2.mp3", srt_key="subs/abc/part_2.srt")
    session.add(part_two)
    await session.commit()
    await session.refresh(part_two)

    monkeypatch.setenv("BLENDER_TEMPLATE_ID", str(uuid.uuid4()))
    monkeypatch.setattr("src.orchestrator.worker.list_keys", AsyncMock(return_value=[]))

    video_id, job_id = uuid.uuid4(), uuid.uuid4()
    _blender_mocks(video_id, job_id)

    await _run_render(session, part_two, run)

    sent = json.loads(respx.calls[0].request.content)
    assert sent["card_key"] == "cards/abc.png"
    assert sent["hook_voice_key"] == "audio/abc/hook.mp3"
