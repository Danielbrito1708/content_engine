from unittest.mock import AsyncMock, patch

from tests.conftest import BUFFER_CREATE_RESPONSE, BUFFER_PENDING_RESPONSE, SAMPLE_REQUEST


async def test_health_ok(client):
    with patch("src.tiktok_poster.api.routes.health.BufferClient") as mock:
        mock.return_value.verify_connection = AsyncMock(return_value=True)
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_health_degraded_when_buffer_unreachable(client):
    with patch("src.tiktok_poster.api.routes.health.BufferClient") as mock:
        mock.return_value.verify_connection = AsyncMock(return_value=False)
        resp = await client.get("/health")
    assert resp.json()["status"] == "degraded"


async def test_schedule_returns_scheduled_at_and_update_id(client):
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        mock_buf.return_value.create_post = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 201
    data = resp.json()
    assert "scheduled_at" in data
    assert data["buffer_update_id"] == "buf_update_001"


async def test_schedule_returns_429_when_queue_full(client):
    full_queue = [{"scheduled_at": 1700000000 + i * 3600} for i in range(10)]
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=full_queue)
        resp = await client.post("/schedule", json=SAMPLE_REQUEST)

    assert resp.status_code == 429
    assert resp.json()["detail"]["error"] == "buffer_queue_full"


async def test_schedule_uses_correct_cta_for_part(client):
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        create_mock = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
        mock_buf.return_value.create_post = create_mock

        await client.post("/schedule", json={**SAMPLE_REQUEST, "part_number": 2})

    caption = create_mock.call_args[0][1]
    assert "Segue para o final" in caption
    assert "Parte 2/2" in caption


async def test_schedule_includes_mandatory_hashtags(client):
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value="https://r2.example.com/video.mp4"),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        create_mock = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
        mock_buf.return_value.create_post = create_mock

        await client.post("/schedule", json=SAMPLE_REQUEST)

    caption = create_mock.call_args[0][1]
    assert "#tiktokbrasil" in caption
    assert "#fyp" in caption


async def test_schedule_passes_presigned_url_to_buffer(client):
    expected_url = "https://account.r2.cloudflarestorage.com/bucket/video.mp4?sig=abc"
    with (
        patch("src.tiktok_poster.api.routes.schedule.BufferClient") as mock_buf,
        patch("src.tiktok_poster.api.routes.schedule.generate_presigned_url",
              new_callable=AsyncMock, return_value=expected_url),
    ):
        mock_buf.return_value.get_pending_posts = AsyncMock(return_value=[])
        create_mock = AsyncMock(return_value=BUFFER_CREATE_RESPONSE)
        mock_buf.return_value.create_post = create_mock

        await client.post("/schedule", json=SAMPLE_REQUEST)

    assert create_mock.call_args[0][0] == expected_url
