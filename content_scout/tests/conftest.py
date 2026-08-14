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
from src.content_scout.db.models import ArchiveCursor, SeenItem
from src.content_scout.sources.reddit import shared_throttle
from src.core import notify as notify_module

NOTIFY_ENV_VARS = (
    "CALLMEBOT_PHONE",
    "CALLMEBOT_APIKEY",
    "CALLMEBOT_BASE_URL",
    "NOTIFY_WEBHOOK_URL",
    "HEALTHCHECK_ALIVE_URL",
    "HEALTHCHECK_SCOUT_URL",
    "HEALTHCHECK_PRODUCED_URL",
)


@pytest.fixture(autouse=True)
def notify_off(monkeypatch):
    """A suíte nunca manda mensagem de verdade — ver o mesmo fixture no orchestrator.

    `bootstrap` chama `load_dotenv()`, então as credenciais do `.env` valeriam
    aqui dentro. Sem destino, `_enabled()` é falso e todo `notify()` é no-op.
    """
    for var in NOTIFY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    notify_module.reset()
    yield
    notify_module.reset()


@pytest.fixture(autouse=True)
def fresh_throttle():
    """The Reddit window is process state, shared by every source instance —
    without this each test would inherit the previous test's timer and wait it
    out for real."""
    shared_throttle(0).reset()
    yield
    shared_throttle(0).reset()


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
        # Cursors outlive seen_items otherwise, and a leftover one makes the next
        # test think its subreddit was already swept — the sweep tests would pass
        # or fail depending on execution order.
        await s.execute(delete(ArchiveCursor))
        await s.commit()
