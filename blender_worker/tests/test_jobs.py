import uuid


async def test_create_job_returns_201(client, video, template, mock_render_job):
    response = await client.post("/jobs", json={
        "video_id": str(video.id),
        "template_id": str(template.id),
    })
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "pending"
    assert data["video_id"] == str(video.id)
    assert data["template_id"] == str(template.id)
    assert data["output_key"] is None
    assert "id" in data


async def test_create_job_with_params(client, video, template, mock_render_job):
    response = await client.post("/jobs", json={
        "video_id": str(video.id),
        "template_id": str(template.id),
        "params": {"fps": 30},
    })
    assert response.status_code == 201
    assert response.json()["params"] == {"fps": 30}


async def test_create_job_video_not_found(client, template, mock_render_job):
    response = await client.post("/jobs", json={
        "video_id": str(uuid.uuid4()),
        "template_id": str(template.id),
    })
    assert response.status_code == 404
    assert response.json()["detail"] == "Video not found"


async def test_create_job_template_not_found(client, video, mock_render_job):
    response = await client.post("/jobs", json={
        "video_id": str(video.id),
        "template_id": str(uuid.uuid4()),
    })
    assert response.status_code == 404
    assert response.json()["detail"] == "Template not found"


async def test_get_job(client, video, template, mock_render_job):
    create = await client.post("/jobs", json={
        "video_id": str(video.id),
        "template_id": str(template.id),
    })
    job_id = create.json()["id"]

    get = await client.get(f"/jobs/{job_id}")
    assert get.status_code == 200
    assert get.json()["id"] == job_id


async def test_get_job_not_found(client):
    response = await client.get(f"/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"
