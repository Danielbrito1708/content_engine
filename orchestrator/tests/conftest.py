import os

os.environ.setdefault("ROOT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from src.core import notify as notify_module
from src.orchestrator.api.app import app  # triggers bootstrap
from src.orchestrator.db.engine import AsyncSessionLocal
from src.orchestrator.db.models import PipelinePart, PipelineRun

#: Todo destino que o notify conhece. Listado uma vez para o fixture abaixo e
#: para os testes que precisam ligar um deles de propósito.
NOTIFY_ENV_VARS = (
    "CALLMEBOT_PHONE",
    "CALLMEBOT_APIKEY",
    "CALLMEBOT_BASE_URL",
    "NOTIFY_WEBHOOK_URL",
    "HEALTHCHECK_ALIVE_URL",
    "HEALTHCHECK_SCOUT_URL",
    "HEALTHCHECK_PRODUCED_URL",
)


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


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch):
    """Retries are exercised in test_http.py; everywhere else the exponential
    backoff is just dead time in front of a test that mocks a 500 on purpose."""
    monkeypatch.setattr("src.orchestrator.clients.http.BACKOFF", 0)


@pytest.fixture(autouse=True)
def notify_off(monkeypatch):
    """A suíte nunca manda mensagem de verdade, nem enche a fila.

    `bootstrap` chama `load_dotenv()`, então quem tiver as credenciais no `.env`
    — o caso normal depois que isto entrar em produção — rodaria a suíte inteira
    disparando WhatsApp. Apagar as vars é o que desliga: `_enabled()` exige um
    destino configurado, então sem elas todo `notify()` é no-op.

    Quem testa o notify de propósito religa a var que precisa; o `reset()` no
    fim garante que a fila não vaze de um teste para o outro.
    """
    for var in NOTIFY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    notify_module.reset()
    yield
    notify_module.reset()


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    yield
    async with AsyncSessionLocal() as s:
        await s.execute(delete(PipelinePart))
        await s.execute(delete(PipelineRun))
        await s.commit()
