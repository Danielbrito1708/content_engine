async def test_health_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["checks"]["db"] == "ok"
    assert data["checks"]["minio"] == "ok"
