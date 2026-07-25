import json

import pytest

from src.core import settings
from src.llm_service.llm.base import BaseLLMClient
from src.llm_service.llm.factory import get_llm_client
from src.llm_service.prompts.moderate import build_user_prompt


class FakeClient(BaseLLMClient):
    def __init__(self, raw: str | Exception):
        self._raw = raw
        self.seen: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.seen.append((system, user))
        if isinstance(self._raw, Exception):
            raise self._raw
        return self._raw


@pytest.fixture
def fake_llm(monkeypatch):
    def _install(raw: str | Exception) -> FakeClient:
        client = FakeClient(raw)
        monkeypatch.setattr(
            "src.llm_service.api.routes.moderate.get_llm_client", lambda model=None: client
        )
        return client

    return _install


async def test_safe_story_returns_safe(client, fake_llm):
    fake_llm(json.dumps({"safe": True}))

    resp = await client.post("/moderate", json={"title": "t", "text": "história comum"})

    assert resp.status_code == 200
    assert resp.json() == {"safe": True, "category": None, "reason": None}


async def test_unsafe_story_returns_category_and_reason(client, fake_llm):
    fake_llm(json.dumps({"safe": False, "category": "self_harm", "reason": "ideação descrita"}))

    resp = await client.post("/moderate", json={"title": "t", "text": "..."})

    body = resp.json()
    assert body["safe"] is False
    assert body["category"] == "self_harm"
    assert body["reason"] == "ideação descrita"


async def test_fenced_json_is_tolerated(client, fake_llm):
    fake_llm('```json\n{"safe": true}\n```')

    resp = await client.post("/moderate", json={"text": "x"})

    assert resp.status_code == 200
    assert resp.json()["safe"] is True


async def test_invalid_json_is_502(client, fake_llm):
    fake_llm("desculpe, não posso avaliar isso")

    resp = await client.post("/moderate", json={"text": "x"})

    assert resp.status_code == 502


async def test_llm_failure_is_502(client, fake_llm):
    fake_llm(RuntimeError("upstream down"))

    resp = await client.post("/moderate", json={"text": "x"})

    assert resp.status_code == 502
    assert "upstream down" in resp.json()["detail"]


async def test_missing_safe_field_is_502(client, fake_llm):
    """A verdict without a decision is not a verdict."""
    fake_llm(json.dumps({"category": "self_harm"}))

    resp = await client.post("/moderate", json={"text": "x"})

    assert resp.status_code == 502


async def test_title_and_text_reach_the_prompt(client, fake_llm):
    fake = fake_llm(json.dumps({"safe": True}))

    await client.post("/moderate", json={"title": "Meu título", "text": "Meu corpo"})

    _system, user = fake.seen[0]
    assert "Meu título" in user
    assert "Meu corpo" in user


def test_user_prompt_omits_header_when_untitled():
    assert build_user_prompt("", "corpo").startswith("HISTÓRIA:")
    assert build_user_prompt("t", "corpo").startswith("TÍTULO: t")


def test_factory_honours_model_override():
    """Moderation must not silently burn the refinement model."""
    assert get_llm_client(model="cheap/model")._model == "cheap/model"


def test_factory_defaults_to_refinement_model():
    assert get_llm_client()._model == settings.env.llm_model


def test_moderation_model_falls_back_to_llm_model():
    """No extra config required — it works out of the box, cheaper if tuned."""
    assert settings.env.llm_moderation_model == settings.env.llm_model


async def test_route_uses_the_moderation_model(client, monkeypatch):
    seen = {}

    def fake_factory(model=None):
        seen["model"] = model
        return FakeClient(json.dumps({"safe": True}))

    monkeypatch.setattr("src.llm_service.api.routes.moderate.get_llm_client", fake_factory)

    await client.post("/moderate", json={"text": "x"})

    assert seen["model"] == settings.env.llm_moderation_model
