"""Fase 1 do multi-account: a conta vira dado (docs/multi_account.md).

`account_id` ausente tem que se comportar exatamente como antes desta coluna
existir — é o requisito de retrocompatibilidade que todo teste aqui protege.
"""

import json
import uuid

import respx
from httpx import Response

from src.orchestrator.db.models import PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.worker import _schedule

SCHEDULE_URL = "http://tiktok_poster:8000/schedule"


async def _make_run(session, account_id=None):
    run = PipelineRun(raw_script="Roteiro.", status=PipelineStatus.processing, account_id=account_id)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _make_part(session, run_id, number=1, video_key="videos/x.mp4"):
    part = PipelinePart(run_id=run_id, part_number=number, script=f"Parte {number}.", video_key=video_key)
    session.add(part)
    await session.commit()
    await session.refresh(part)
    return part


# ── API /accounts ────────────────────────────────────────────────────────


async def test_create_account_returns_201(client):
    resp = await client.post("/accounts", json={"slug": "conta_2"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "conta_2"
    assert data["status"] == "active"
    assert data["template_id"] is None


async def test_create_account_duplicate_slug_is_conflict(client):
    await client.post("/accounts", json={"slug": "conta_2"})
    resp = await client.post("/accounts", json={"slug": "conta_2"})
    assert resp.status_code == 409


async def test_list_accounts(client):
    await client.post("/accounts", json={"slug": "conta_a"})
    await client.post("/accounts", json={"slug": "conta_b"})

    resp = await client.get("/accounts")
    assert resp.status_code == 200
    slugs = {a["slug"] for a in resp.json()}
    assert {"conta_a", "conta_b"} <= slugs


async def test_get_account_not_found(client):
    resp = await client.get(f"/accounts/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_get_account_returns_created_account(client):
    create = await client.post("/accounts", json={"slug": "conta_c", "notes": "segunda conta"})
    account_id = create.json()["id"]

    resp = await client.get(f"/accounts/{account_id}")
    assert resp.status_code == 200
    assert resp.json()["notes"] == "segunda conta"


# ── POST /pipeline com account_id ───────────────────────────────────────


async def test_create_pipeline_without_account_id_omits_it(client, mock_run_pipeline):
    resp = await client.post("/pipeline", json={"script": "Script."})
    assert resp.status_code == 201
    assert resp.json()["account_id"] is None


async def test_create_pipeline_persists_account_id(client, mock_run_pipeline):
    account = await client.post("/accounts", json={"slug": "conta_d"})
    account_id = account.json()["id"]

    resp = await client.post("/pipeline", json={"script": "Script.", "account_id": account_id})
    assert resp.status_code == 201
    assert resp.json()["account_id"] == account_id

    get = await client.get(f"/pipeline/{resp.json()['id']}")
    assert get.json()["account_id"] == account_id


# ── `_schedule` repassa (ou omite) `account_id` no payload ──────────────


@respx.mock
async def test_schedule_omits_account_id_when_run_has_none(session):
    run = await _make_run(session, account_id=None)
    await _make_part(session, run.id)

    route = respx.post(SCHEDULE_URL).mock(
        return_value=Response(200, json={"scheduled_at": "2026-06-01T20:00:00Z", "buffer_update_id": "buf_1"})
    )
    await _schedule(session, run)

    sent = json.loads(route.calls[0].request.content)
    assert "account_id" not in sent


@respx.mock
async def test_schedule_sends_account_id_when_run_has_one(session):
    account_id = uuid.uuid4()
    run = await _make_run(session, account_id=account_id)
    await _make_part(session, run.id)

    route = respx.post(SCHEDULE_URL).mock(
        return_value=Response(200, json={"scheduled_at": "2026-06-01T20:00:00Z", "buffer_update_id": "buf_1"})
    )
    await _schedule(session, run)

    sent = json.loads(route.calls[0].request.content)
    assert sent["account_id"] == str(account_id)
