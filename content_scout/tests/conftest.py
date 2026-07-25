import os

os.environ.setdefault("ROOT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5433/content_scout"
)
# The periodic loop must never start under test — it would fire real HTTP calls.
os.environ.setdefault("SCOUT_ENABLED", "false")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from src.content_scout.api.app import app  # triggers bootstrap
from src.content_scout.db.engine import AsyncSessionLocal
from src.content_scout.db.models import SeenItem


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session():
    async with AsyncSessionLocal() as s:
        yield s


@pytest_asyncio.fixture(autouse=True)
async def clean_db(request):
    yield
    if request.node.get_closest_marker("no_db"):
        return
    async with AsyncSessionLocal() as s:
        await s.execute(delete(SeenItem))
        await s.commit()
