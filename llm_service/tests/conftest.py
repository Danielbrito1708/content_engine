import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ROOT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LLM_PROVIDER", "openrouter")
os.environ.setdefault("LLM_MODEL", "anthropic/claude-3.5-sonnet")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from src.llm_service.api.app import app  # noqa: E402 — env must be set first


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


VALID_REFINE_RESPONSE = {
    "parts": [
        "Você não vai acreditar no que ela descobriu no celular dele... Na parte 1 da história.",
        "Na parte anterior, ela descobriu as mensagens. Agora veja o que ela fez a seguir.",
    ],
    "classification": {
        "content_type": "drama",
        "tone": "suspenseful",
        "target_audience": {
            "age_range": [15, 25],
            "gender": "female",
            "interests": ["relationships", "drama"],
        },
        "cta_per_part": [
            "Comenta o que você faria no lugar dela 👇",
            "Segue para não perder o final 🔥",
        ],
        "hashtag_hints": ["#traição", "#relacionamento", "#drama", "#tiktokbrasil"],
        "split_rationale": "Cliffhanger no momento da descoberta para maximizar retenção.",
    },
}
