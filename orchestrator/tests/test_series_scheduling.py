"""Partes de uma mesma história saem encadeadas, não espalhadas no calendário.

O formato mudou: a história completa vai num vídeo só e dividir é a exceção.
Quando divide, as partes são uma continuação — a parte N pendura no horário já
agendado da parte N-1 (`follows_at`), e é o poster que aplica o intervalo. Só a
parte 1 disputa os horários preferidos.
"""

import json
from datetime import datetime, timezone

import respx
from httpx import Response

from src.orchestrator.db.models import PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.worker import _schedule

SCHEDULE_URL = "http://tiktok_poster:8000/schedule"


async def _make_run(session, classification=None):
    run = PipelineRun(
        raw_script="Roteiro.",
        status=PipelineStatus.processing,
        classification=classification or {},
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _make_part(session, run_id, number, video_key="videos/x.mp4", scheduled_at=None):
    part = PipelinePart(
        run_id=run_id,
        part_number=number,
        script=f"Parte {number}.",
        video_key=video_key,
        scheduled_at=scheduled_at,
    )
    session.add(part)
    await session.commit()
    await session.refresh(part)
    return part


def _slots(*isoformats):
    """Poster mock que devolve um horário diferente a cada chamada."""
    return [
        Response(200, json={"scheduled_at": ts, "buffer_update_id": f"buf_{i}"})
        for i, ts in enumerate(isoformats)
    ]


def _sent(route):
    return [json.loads(call.request.content) for call in route.calls]


@respx.mock
async def test_first_part_carries_no_anchor(session):
    """Parte 1 é a que ancora: vai sem `follows_at` e cai no calendário."""
    run = await _make_run(session)
    await _make_part(session, run.id, 1)

    route = respx.post(SCHEDULE_URL).mock(side_effect=_slots("2026-06-01T20:00:00Z"))
    await _schedule(session, run)

    assert _sent(route)[0].get("follows_at") is None


@respx.mock
async def test_later_parts_anchor_on_the_previous_slot(session):
    run = await _make_run(session)
    for n in (1, 2, 3):
        await _make_part(session, run.id, n)

    route = respx.post(SCHEDULE_URL).mock(
        side_effect=_slots(
            "2026-06-01T20:00:00Z",
            "2026-06-01T20:30:00Z",
            "2026-06-01T21:00:00Z",
        )
    )
    await _schedule(session, run)

    sent = _sent(route)
    assert sent[0].get("follows_at") is None
    assert sent[1]["follows_at"] == "2026-06-01T20:00:00+00:00"
    assert sent[2]["follows_at"] == "2026-06-01T20:30:00+00:00"


@respx.mock
async def test_resumed_run_anchors_on_the_part_already_booked(session):
    """Parte 1 já agendada antes de um restart: a parte 2 pendura nela.

    Sem isso a continuação voltaria a disputar os horários preferidos e a
    segunda metade da história sairia horas depois da primeira.
    """
    run = await _make_run(session)
    await _make_part(
        session, run.id, 1, scheduled_at=datetime(2026, 6, 1, 20, tzinfo=timezone.utc)
    )
    await _make_part(session, run.id, 2)

    route = respx.post(SCHEDULE_URL).mock(side_effect=_slots("2026-06-01T20:30:00Z"))
    await _schedule(session, run)

    sent = _sent(route)
    assert len(sent) == 1, "a parte já agendada não pode ser reagendada"
    assert sent[0]["part_number"] == 2
    assert sent[0]["follows_at"] == "2026-06-01T20:00:00+00:00"


@respx.mock
async def test_total_parts_comes_from_the_run_not_the_classification(session):
    """`classification["parts"]` nunca foi preenchido pelo `llm_service`.

    Quem sabe quantas partes existem é o orchestrador, que as criou — a caption
    dizia "1/1" mesmo em série dividida enquanto o poster lia a chave errada.
    """
    run = await _make_run(session, classification={"parts": 9, "hashtag_hints": []})
    await _make_part(session, run.id, 1)
    await _make_part(session, run.id, 2)

    route = respx.post(SCHEDULE_URL).mock(
        side_effect=_slots("2026-06-01T20:00:00Z", "2026-06-01T20:30:00Z")
    )
    await _schedule(session, run)

    assert [s["total_parts"] for s in _sent(route)] == [2, 2]


@respx.mock
async def test_single_part_run_reports_one_total(session):
    """O caso padrão do formato novo: história inteira, um vídeo."""
    run = await _make_run(session)
    await _make_part(session, run.id, 1)

    route = respx.post(SCHEDULE_URL).mock(side_effect=_slots("2026-06-01T20:00:00Z"))
    await _schedule(session, run)

    assert _sent(route)[0]["total_parts"] == 1


@respx.mock
async def test_part_without_video_does_not_become_the_anchor(session):
    """Parte pulada por não ter render não desloca a âncora da seguinte."""
    run = await _make_run(session)
    await _make_part(session, run.id, 1, scheduled_at=datetime(2026, 6, 1, 20, tzinfo=timezone.utc))
    await _make_part(session, run.id, 2, video_key=None)
    await _make_part(session, run.id, 3)

    route = respx.post(SCHEDULE_URL).mock(side_effect=_slots("2026-06-01T20:30:00Z"))
    await _schedule(session, run)

    sent = _sent(route)
    assert [s["part_number"] for s in sent] == [3]
    assert sent[0]["follows_at"] == "2026-06-01T20:00:00+00:00"
