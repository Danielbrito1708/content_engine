import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

os.environ.setdefault("ROOT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BUFFER_ACCESS_TOKEN", "test-token")
os.environ.setdefault("BUFFER_PROFILE_ID", "test-profile-id")
os.environ.setdefault("MINIO_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minioadmin")
os.environ.setdefault("MINIO_SECRET_KEY", "minioadmin")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5433/tiktok_poster_test")
os.environ.setdefault("ACCOUNT_CREDENTIALS_KEY", "PPDtovE96TaJj6iQL-C_gT2Eu4IQO8tNMQtOW_pgnyU=")

from src.tiktok_poster.api.app import app  # noqa: E402
from src.tiktok_poster.db.engine import AsyncSessionLocal  # noqa: E402
from src.tiktok_poster.db.models import PostMetric, Publication  # noqa: E402


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session():
    async with AsyncSessionLocal() as s:
        yield s


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    """Apaga `publications`/`post_metrics` entre testes.

    Necessário desde que `POST /schedule` passou a gravar uma `Publication` no
    caminho de sucesso: várias respostas mockadas do Buffer neste arquivo (e em
    `test_youtube.py`) reusam o mesmo `buffer_post_id` ("buf_update_001") em
    testes diferentes, e a coluna é `unique` — sem limpar entre testes, o
    segundo teste que agenda com sucesso bateria de frente com a linha que o
    primeiro deixou para trás. Mesmo padrão do `clean_db` do orchestrator.

    **A limpeza é best-effort.** Autouse vale para a suíte inteira, incluindo
    `test_hashtags.py`/`test_scheduler.py`/`test_posting_window.py`, que
    testam função pura e nunca tocam `Publication`/`PostMetric` — sem o
    `try/except`, um Postgres fora do ar derrubava esses testes no teardown
    mesmo eles nunca tendo escrito nada. Quando o banco está de pé, a limpeza
    acontece normalmente; sem ele, não há nada para limpar mesmo.
    """
    yield
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(PostMetric))
            await s.execute(delete(Publication))
            await s.commit()
    except Exception:  # noqa: BLE001 — teardown de teste puro não pode falhar por isto
        pass


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
