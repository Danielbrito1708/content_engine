"""O clima emocional da história — o campo que escolhe a trilha sonora do vídeo.

LLM sempre mockado, como no resto da suíte.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.llm_service.prompts.refine import SYSTEM_PROMPT, build_user_prompt
from src.llm_service.schemas.refine import MOODS, RefineResponse, normalize_mood
from tests.conftest import VALID_REFINE_RESPONSE


# --- normalize_mood ------------------------------------------------------------


@pytest.mark.parametrize("value", ["sad", "tense", "hopeful", "neutral"])
def test_the_four_valid_values_pass_through(value):
    assert normalize_mood(value) == value


@pytest.mark.parametrize("value", ["SAD", " Tense ", "Hopeful"])
def test_case_and_spacing_are_normalized(value):
    assert normalize_mood(value) in MOODS
    assert normalize_mood(value) == value.strip().lower()


@pytest.mark.parametrize("value", [None, "", "happy", "triste", "1"])
def test_anything_else_becomes_neutral(value):
    """O campo sai de um LLM: um valor fora do contrato não pode derrubar o
    refino de um roteiro que está inteiro e correto."""
    assert normalize_mood(value) == "neutral"


# --- RefineResponse ----------------------------------------------------------


def test_response_defaults_to_neutral_when_the_model_omits_the_field():
    """Modelo que ignora o campo novo cai na trilha neutra — a mesma que todo
    vídeo já usava antes deste campo existir."""
    assert RefineResponse.model_validate(VALID_REFINE_RESPONSE).mood == "neutral"


@pytest.mark.parametrize("value,expected", [("sad", "sad"), ("TENSE", "tense"),
                                            ("feliz", "neutral")])
def test_response_normalizes_what_the_model_returned(value, expected):
    payload = {**VALID_REFINE_RESPONSE, "mood": value}
    assert RefineResponse.model_validate(payload).mood == expected


def test_mood_is_not_the_classification_tone():
    """São duas perguntas diferentes: tone informa hashtag/edição (funny,
    educational, shocking...) e não mapeia para música; mood só escolhe a
    trilha sonora. Uma história pode ser 'emotional' em tone e 'sad' em mood."""
    payload = {**VALID_REFINE_RESPONSE, "mood": "sad"}
    response = RefineResponse.model_validate(payload)
    assert response.mood == "sad"
    assert response.classification.tone == "suspenseful"


# --- prompt ------------------------------------------------------------------


def test_prompt_asks_for_the_mood():
    assert "REGRA DO MOOD" in SYSTEM_PROMPT
    assert '"sad"' in SYSTEM_PROMPT
    assert '"tense"' in SYSTEM_PROMPT
    assert '"hopeful"' in SYSTEM_PROMPT
    assert '"neutral"' in SYSTEM_PROMPT


def test_prompt_prefers_neutral_to_a_guess():
    mood_rules = SYSTEM_PROMPT.split("REGRA DO MOOD")[1]
    assert "neutral" in mood_rules
    assert "dúvida" in mood_rules


def test_json_example_carries_the_field_outside_classification():
    example = build_user_prompt("roteiro", {})
    assert '"mood"' in example
    # Antes de "classification" abrir: é campo de topo, como o hook.
    assert example.index('"mood"') < example.index('"classification"')


# --- endpoint ----------------------------------------------------------------


async def test_endpoint_returns_the_mood(client):
    payload = {**VALID_REFINE_RESPONSE, "mood": "sad"}
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(return_value=RefineResponse.model_validate(payload))

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client):
        resp = await client.post("/refine", json={"script": "Ela perdeu tudo naquele dia."})

    assert resp.status_code == 200
    assert resp.json()["mood"] == "sad"


async def test_endpoint_reports_neutral_when_the_model_says_nothing(client):
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(
        return_value=RefineResponse.model_validate(VALID_REFINE_RESPONSE)
    )

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client):
        resp = await client.post("/refine", json={"script": "Texto qualquer."})

    assert resp.json()["mood"] == "neutral"
