import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from src.blender_worker.api.app import app  # triggers bootstrap
from src.blender_worker.db.engine import AsyncSessionLocal
from src.blender_worker.db.models import Job, Template, Video


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session():
    async with AsyncSessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def video(session):
    v = Video(
        video_file_key="test/video.mp4",
        music_key="test/music.mp3",
        voice_key="test/voice.mp3",
        subtitle_key="test/subs.srt",
    )
    session.add(v)
    await session.commit()
    await session.refresh(v)
    return v


@pytest_asyncio.fixture
async def template(session):
    t = Template(
        name="Test Template",
        blend_key="test/template.blend",
        json_key="test/template.json",
        # Render-ready by default — see worker.py's VSEL cutover (08/09/2026).
        # A template with no yaml_key is a valid, deliberate state (see
        # test_templates.py), but the shared fixture should represent a
        # complete row so worker.py tests don't each have to set it.
        yaml_key="test/template.yaml",
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return t


@pytest.fixture
def mock_render_job(monkeypatch):
    async def noop(_job_id):
        pass
    monkeypatch.setattr("src.blender_worker.api.routes.jobs.render_job", noop)


@pytest_asyncio.fixture(autouse=True)
async def clean_db(request):
    yield
    if "no_db" in request.keywords:
        return
    async with AsyncSessionLocal() as s:
        await s.execute(delete(Job))
        await s.execute(delete(Video))
        await s.execute(delete(Template))
        await s.commit()
