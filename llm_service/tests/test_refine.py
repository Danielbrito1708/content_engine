import json
from unittest.mock import AsyncMock, patch

from tests.conftest import VALID_REFINE_RESPONSE


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_refine_returns_parts_and_classification(client):
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(
        return_value=__import__(
            "src.llm_service.schemas.refine", fromlist=["RefineResponse"]
        ).RefineResponse.model_validate(VALID_REFINE_RESPONSE)
    )

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client):
        resp = await client.post("/refine", json={"script": "Era uma vez uma garota que descobriu tudo."})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["parts"]) == 2
    assert data["classification"]["content_type"] == "drama"
    assert data["classification"]["target_audience"]["age_range"] == [15, 25]
    assert len(data["classification"]["hashtag_hints"]) >= 1


async def test_refine_single_part_when_script_is_short(client):
    single_part_response = {**VALID_REFINE_RESPONSE, "parts": ["Roteiro curto refinado."]}
    single_part_response["classification"] = {
        **VALID_REFINE_RESPONSE["classification"],
        "cta_per_part": ["Segue para mais! 🔥"],
        "split_rationale": None,
    }

    from src.llm_service.schemas.refine import RefineResponse
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(return_value=RefineResponse.model_validate(single_part_response))

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client):
        resp = await client.post("/refine", json={"script": "Texto curto."})

    assert resp.status_code == 200
    assert len(resp.json()["parts"]) == 1
    assert resp.json()["classification"]["split_rationale"] is None


async def test_refine_passes_metadata_to_llm(client):
    from src.llm_service.schemas.refine import RefineResponse
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(return_value=RefineResponse.model_validate(VALID_REFINE_RESPONSE))

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client) as mock_factory:
        with patch("src.llm_service.api.routes.refine.build_user_prompt", wraps=__import__(
            "src.llm_service.prompts.refine", fromlist=["build_user_prompt"]
        ).build_user_prompt) as mock_prompt:
            await client.post(
                "/refine",
                json={"script": "Roteiro.", "metadata": {"source": "reddit", "subreddit": "relacionamentos"}},
            )
            call_args = mock_prompt.call_args
            assert call_args[0][1] == {"source": "reddit", "subreddit": "relacionamentos"}


async def test_refine_returns_502_on_llm_error(client):
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(side_effect=RuntimeError("API timeout"))

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client):
        resp = await client.post("/refine", json={"script": "Qualquer roteiro."})

    assert resp.status_code == 502


async def test_refine_returns_502_on_invalid_json(client):
    import json as _json
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(side_effect=_json.JSONDecodeError("err", "", 0))

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client):
        resp = await client.post("/refine", json={"script": "Qualquer roteiro."})

    assert resp.status_code == 502
    assert "invalid JSON" in resp.json()["detail"]


async def test_factory_openrouter(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_MODEL", "anthropic/claude-3.5-sonnet")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    from src.llm_service.llm.factory import get_llm_client
    from src.llm_service.llm.openai_compat import OpenAICompatClient
    client = get_llm_client()
    assert isinstance(client, OpenAICompatClient)


async def test_factory_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    from src.llm_service.llm.anthropic_client import AnthropicClient
    from src.llm_service.llm.factory import get_llm_client
    client = get_llm_client()
    assert isinstance(client, AnthropicClient)


async def test_factory_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "invalid")
    import pytest
    from src.llm_service.llm.factory import get_llm_client
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_llm_client()
