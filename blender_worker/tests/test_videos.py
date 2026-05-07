import uuid


async def test_create_video_returns_201(client):
    response = await client.post("/videos", json={
        "video_file_key": "videos/ep01/bg.mp4",
        "music_key": "videos/ep01/music.mp3",
        "voice_key": "videos/ep01/voice.mp3",
        "subtitle_key": "videos/ep01/subs.srt",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["video_file_key"] == "videos/ep01/bg.mp4"
    assert data["video_metadata"] is None
    assert "id" in data


async def test_create_video_with_metadata(client):
    response = await client.post("/videos", json={
        "video_file_key": "videos/ep01/bg.mp4",
        "music_key": "videos/ep01/music.mp3",
        "voice_key": "videos/ep01/voice.mp3",
        "subtitle_key": "videos/ep01/subs.srt",
        "video_metadata": {"title": "EP01", "fps": 30},
    })
    assert response.status_code == 201
    assert response.json()["video_metadata"] == {"title": "EP01", "fps": 30}


async def test_get_video(client):
    create = await client.post("/videos", json={
        "video_file_key": "v.mp4",
        "music_key": "m.mp3",
        "voice_key": "vo.mp3",
        "subtitle_key": "s.srt",
    })
    vid_id = create.json()["id"]

    get = await client.get(f"/videos/{vid_id}")
    assert get.status_code == 200
    assert get.json()["id"] == vid_id


async def test_get_video_not_found(client):
    response = await client.get(f"/videos/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Video not found"
