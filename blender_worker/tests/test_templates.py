import uuid


async def test_create_template_returns_201(client):
    response = await client.post("/templates", json={
        "name": "Reels 30s",
        "blend_key": "templates/reels-30s.blend",
        "json_key": "templates/reels-30s.json",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Reels 30s"
    assert data["blend_key"] == "templates/reels-30s.blend"
    assert "id" in data


async def test_get_template(client):
    create = await client.post("/templates", json={
        "name": "T1",
        "blend_key": "templates/t1.blend",
        "json_key": "templates/t1.json",
    })
    tmpl_id = create.json()["id"]

    get = await client.get(f"/templates/{tmpl_id}")
    assert get.status_code == 200
    assert get.json()["id"] == tmpl_id


async def test_get_template_not_found(client):
    response = await client.get(f"/templates/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Template not found"
