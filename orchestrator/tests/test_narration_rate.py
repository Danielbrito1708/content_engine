import json
import uuid

import respx
from httpx import Response

from src.core import settings
from src.orchestrator.clients.blender import BlenderClient
from src.orchestrator.clients.tts import TTSClient
from src.orchestrator.db.models import PipelinePart, PipelineRun
from src.orchestrator.worker import _narration_rate, _run_tts

TEMPLATE_ID = settings.env.blender_template_id
CONFIG_URL = f"http://blender_worker:8000/templates/{TEMPLATE_ID}/config"
TTS_URL = "http://tts_service:8000/generate"


async def _make_run(session):
    run = PipelineRun(raw_script="Roteiro.")
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


def _tts_ok(run_id):
    return Response(
        201,
        json={
            "audio_key": f"audio/{run_id}/part_1.mp3",
            "srt_key": f"subs/{run_id}/part_1.srt",
        },
    )


# ── BlenderClient.get_template_config ────────────────────────────

@respx.mock
async def test_client_fetches_template_config():
    config = {"frame_rate": 30, "narration": {"rate": "+20%"}}
    respx.get(CONFIG_URL).mock(return_value=Response(200, json=config))

    got = await BlenderClient().get_template_config(uuid.UUID(TEMPLATE_ID))

    assert got == config


# ── _narration_rate ──────────────────────────────────────────────

@respx.mock
async def test_narration_rate_from_template():
    respx.get(CONFIG_URL).mock(
        return_value=Response(200, json={"narration": {"rate": "+25%"}})
    )
    assert await _narration_rate() == "+25%"


@respx.mock
async def test_narration_rate_accepts_negative():
    respx.get(CONFIG_URL).mock(
        return_value=Response(200, json={"narration": {"rate": "-10%"}})
    )
    assert await _narration_rate() == "-10%"


@respx.mock
async def test_narration_rate_none_without_narration_block():
    """A template predating the block keeps the tts_service default."""
    respx.get(CONFIG_URL).mock(return_value=Response(200, json={"frame_rate": 30}))
    assert await _narration_rate() is None


@respx.mock
async def test_narration_rate_none_when_block_has_no_rate():
    respx.get(CONFIG_URL).mock(return_value=Response(200, json={"narration": {}}))
    assert await _narration_rate() is None


@respx.mock
async def test_narration_rate_none_when_narration_is_null():
    respx.get(CONFIG_URL).mock(return_value=Response(200, json={"narration": None}))
    assert await _narration_rate() is None


@respx.mock
async def test_narration_rate_degrades_on_http_error():
    """Narration speed is cosmetic — a 502 must not fail the run."""
    respx.get(CONFIG_URL).mock(return_value=Response(502, json={"detail": "boom"}))
    assert await _narration_rate() is None


@respx.mock
async def test_narration_rate_degrades_on_404():
    respx.get(CONFIG_URL).mock(return_value=Response(404, json={"detail": "not found"}))
    assert await _narration_rate() is None


@respx.mock
async def test_narration_rate_degrades_when_service_unreachable():
    respx.get(CONFIG_URL).mock(side_effect=ConnectionError("blender_worker down"))
    assert await _narration_rate() is None


# ── TTSClient payload ────────────────────────────────────────────

@respx.mock
async def test_tts_client_sends_rate():
    route = respx.post(TTS_URL).mock(return_value=_tts_ok("r1"))

    await TTSClient().generate(text="oi", run_id="r1", part_number=1, rate="+30%")

    assert json.loads(route.calls.last.request.content)["rate"] == "+30%"


@respx.mock
async def test_tts_client_omits_rate_when_none():
    """Omitted, not null — the tts_service applies its own TTS_RATE."""
    route = respx.post(TTS_URL).mock(return_value=_tts_ok("r1"))

    await TTSClient().generate(text="oi", run_id="r1", part_number=1)

    assert "rate" not in json.loads(route.calls.last.request.content)


# ── _run_tts ─────────────────────────────────────────────────────

@respx.mock
async def test_run_tts_forwards_template_rate(session):
    run = await _make_run(session)
    part = await _make_part(session, run.id)
    route = respx.post(TTS_URL).mock(return_value=_tts_ok(run.id))

    await _run_tts(session, part, run, "+35%")

    assert json.loads(route.calls.last.request.content)["rate"] == "+35%"


@respx.mock
async def test_run_tts_without_rate_omits_it(session):
    run = await _make_run(session)
    part = await _make_part(session, run.id)
    route = respx.post(TTS_URL).mock(return_value=_tts_ok(run.id))

    await _run_tts(session, part, run)

    assert "rate" not in json.loads(route.calls.last.request.content)
