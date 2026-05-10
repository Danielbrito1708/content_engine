import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ROOT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TTS_PROVIDER", "edge")
os.environ.setdefault("TTS_VOICE", "pt-BR-ThalitaNeural")
os.environ.setdefault("MINIO_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minioadmin")
os.environ.setdefault("MINIO_SECRET_KEY", "minioadmin")
os.environ.setdefault("REMOVE_SILENCE", "false")

from src.tts_service.api.app import app  # noqa: E402


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


SAMPLE_REQUEST = {
    "text": "Você não vai acreditar no que ela descobriu no celular dele.",
    "run_id": "550e8400-e29b-41d4-a716-446655440000",
    "part_number": 1,
}

FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 100
