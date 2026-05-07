import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ROOT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BUFFER_ACCESS_TOKEN", "test-token")
os.environ.setdefault("BUFFER_PROFILE_ID", "test-profile-id")
os.environ.setdefault("MINIO_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minioadmin")
os.environ.setdefault("MINIO_SECRET_KEY", "minioadmin")

from src.tiktok_poster.api.app import app  # noqa: E402


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


SAMPLE_CLASSIFICATION = {
    "content_type": "drama",
    "tone": "suspenseful",
    "target_audience": {"age_range": [15, 25], "gender": "female", "interests": ["relationships"]},
    "cta_per_part": ["Comenta o que você faria 👇", "Segue para o final 🔥"],
    "hashtag_hints": ["#traição", "#relacionamento", "#drama"],
    "split_rationale": "Cliffhanger na descoberta.",
    "parts": 2,
}

SAMPLE_REQUEST = {
    "video_key": "outputs/abc123.mp4",
    "classification": SAMPLE_CLASSIFICATION,
    "part_number": 1,
    "series_id": "550e8400-e29b-41d4-a716-446655440000",
}

BUFFER_PENDING_RESPONSE = {"updates": []}

BUFFER_CREATE_RESPONSE = {
    "success": True,
    "updates": [{"id": "buf_update_001", "scheduled_at": 1700000000}],
}
