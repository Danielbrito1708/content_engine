import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from src.orchestrator.api.app import app  # triggers bootstrap
from src.orchestrator.db.engine import AsyncSessionLocal
from src.orchestrator.db.models import PipelinePart, PipelineRun


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session():
    async with AsyncSessionLocal() as s:
        yield s


@pytest.fixture
def mock_run_pipeline(monkeypatch):
    async def noop(_run_id):
        pass
    monkeypatch.setattr("src.orchestrator.api.routes.pipeline.run_pipeline", noop)


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    yield
    async with AsyncSessionLocal() as s:
        await s.execute(delete(PipelinePart))
        await s.execute(delete(PipelineRun))
        await s.commit()
