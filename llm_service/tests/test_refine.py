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
    # O fixture ainda traz duas partes (é resposta de modelo desobediente); o
    # formato curto junta tudo num vídeo só. Ver `test_split_policy.py`.
    assert len(data["parts"]) == 1
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


def _patch_provider(monkeypatch, provider: str):
    """Swap the settings object the factory reads.

    ``monkeypatch.setenv`` alone does nothing here: settings are loaded once at
    bootstrap into a frozen model, so the factory never re-reads the environment.
    """
    from types import SimpleNamespace

    from src.core import settings

    stub = SimpleNamespace(env=SimpleNamespace(**{**settings.env.model_dump(),
                                                 "llm_provider": provider}))
    monkeypatch.setattr("src.llm_service.llm.factory.settings", stub)


async def test_factory_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _patch_provider(monkeypatch, "anthropic")

    from src.llm_service.llm.anthropic_client import AnthropicClient
    from src.llm_service.llm.factory import get_llm_client
    client = get_llm_client()
    assert isinstance(client, AnthropicClient)


async def test_factory_unknown_provider_raises(monkeypatch):
    import pytest
    _patch_provider(monkeypatch, "invalid")

    from src.llm_service.llm.factory import get_llm_client
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_llm_client()


# --- o texto narrado termina numa pergunta ------------------------------------


def test_prompt_ends_the_narration_on_a_first_person_question():
    """A pergunta é a última coisa narrada — e é a única finalização permitida.

    Reverte a regra anterior ("sem CTA no texto narrado"), que existia porque o
    CTA de então era genérico e vinha *depois* do desfecho. O CTA agora é a
    decisão em aberto da própria história, então ele não fecha o vídeo: ele é o
    ponto em que a história para.
    """
    from src.llm_service.prompts.refine import SYSTEM_PROMPT

    assert "PRIMEIRA PESSOA" in SYSTEM_PROMPT
    assert "devo me separar?" in SYSTEM_PROMPT
    assert "cta_per_part" in SYSTEM_PROMPT


def test_prompt_still_forbids_the_generic_sign_off():
    """O que foi removido em 27/08 continua removido: despedida, moral, "e é isso"."""
    from src.llm_service.prompts.refine import SYSTEM_PROMPT

    assert "Cada parte termina com um CTA" not in SYSTEM_PROMPT
    assert "Sem despedida, sem moral" in SYSTEM_PROMPT
    assert "sem pedir like/follow" in SYSTEM_PROMPT


def test_prompt_requires_the_question_to_be_story_specific():
    """"comenta o que você faria" serve para qualquer vídeo — é o que se evita."""
    from src.llm_service.prompts.refine import SYSTEM_PROMPT

    assert "nunca uma frase que serviria" in SYSTEM_PROMPT


def test_prompt_ties_the_question_to_an_unresolved_decision():
    """Perguntar "devo me separar?" numa história que já terminou na separação
    é incoerente — a condensação tem que parar no ponto da decisão."""
    from src.llm_service.prompts.refine import SYSTEM_PROMPT

    assert "AINDA ESTÁ ABERTA" in SYSTEM_PROMPT


def test_prompt_still_asks_for_the_cta_field():
    from src.llm_service.prompts.refine import build_user_prompt

    assert "cta_per_part" in build_user_prompt("roteiro", {})
