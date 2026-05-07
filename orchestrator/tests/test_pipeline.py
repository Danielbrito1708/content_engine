import pytest


async def test_create_pipeline_returns_201(client, mock_run_pipeline):
    resp = await client.post("/pipeline", json={"script": "Um script de teste.", "metadata": {}})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    assert data["parts"] == []
    assert "id" in data


async def test_create_pipeline_starts_background_task(client, mock_run_pipeline):
    resp = await client.post("/pipeline", json={"script": "Script qualquer."})
    assert resp.status_code == 201


async def test_get_pipeline_not_found(client):
    import uuid
    resp = await client.get(f"/pipeline/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_get_pipeline_returns_run(client, mock_run_pipeline):
    create = await client.post("/pipeline", json={"script": "Script."})
    run_id = create.json()["id"]

    get = await client.get(f"/pipeline/{run_id}")
    assert get.status_code == 200
    assert get.json()["id"] == run_id


async def test_list_pipelines(client, mock_run_pipeline):
    await client.post("/pipeline", json={"script": "Script 1."})
    await client.post("/pipeline", json={"script": "Script 2."})

    resp = await client.get("/pipeline")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


async def test_list_pipelines_pagination(client, mock_run_pipeline):
    for i in range(3):
        await client.post("/pipeline", json={"script": f"Script {i}."})

    resp = await client.get("/pipeline?limit=2&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()) == 2
