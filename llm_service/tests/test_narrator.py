"""O gênero de quem narra a história — o campo que escolhe a voz do vídeo.

LLM sempre mockado, como no resto da suíte.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.llm_service.prompts.refine import SYSTEM_PROMPT, build_user_prompt
from src.llm_service.schemas.refine import (
    NARRATOR_GENDERS,
    RefineResponse,
    normalize_narrator_gender,
)
from tests.conftest import VALID_REFINE_RESPONSE


# --- normalize_narrator_gender ----------------------------------------------


@pytest.mark.parametrize("value", ["male", "female", "unknown"])
def test_the_three_valid_values_pass_through(value):
    assert normalize_narrator_gender(value) == value


@pytest.mark.parametrize("value", ["MALE", " Female ", "Unknown"])
def test_case_and_spacing_are_normalized(value):
    assert normalize_narrator_gender(value) in NARRATOR_GENDERS
    assert normalize_narrator_gender(value) == value.strip().lower()


@pytest.mark.parametrize("value", [None, "", "masculino", "m", "all", "não sei", "1"])
def test_anything_else_becomes_unknown(value):
    """O campo sai de um LLM: um valor fora do contrato não pode derrubar o
    refino de um roteiro que está inteiro e correto."""
    assert normalize_narrator_gender(value) == "unknown"


# --- RefineResponse ----------------------------------------------------------


def test_response_defaults_to_unknown_when_the_model_omits_the_field():
    """Nem todo modelo obedece, e história sem narrador identificável existe —
    os dois casos caem na voz padrão."""
    assert RefineResponse.model_validate(VALID_REFINE_RESPONSE).narrator_gender == "unknown"


@pytest.mark.parametrize("value,expected", [("male", "male"), ("FEMALE", "female"),
                                            ("masculino", "unknown")])
def test_response_normalizes_what_the_model_returned(value, expected):
    payload = {**VALID_REFINE_RESPONSE, "narrator_gender": value}
    assert RefineResponse.model_validate(payload).narrator_gender == expected


def test_narrator_gender_is_not_the_target_audience():
    """São duas perguntas diferentes: quem conta e para quem se conta. Confundir
    as duas narra história de homem com voz de mulher sempre que o público for
    feminino."""
    payload = {**VALID_REFINE_RESPONSE, "narrator_gender": "male"}
    response = RefineResponse.model_validate(payload)
    assert response.narrator_gender == "male"
    assert response.classification.target_audience.gender == "female"


# --- prompt ------------------------------------------------------------------


def test_prompt_asks_for_the_narrator_gender():
    assert "narrator_gender" in SYSTEM_PROMPT
    assert '"male" | "female" | "unknown"' in SYSTEM_PROMPT


def test_prompt_separates_narrator_from_audience():
    assert "target_audience" in SYSTEM_PROMPT.split("REGRA DO NARRADOR")[1]


def test_prompt_prefers_unknown_to_a_guess():
    narrator_rules = SYSTEM_PROMPT.split("REGRA DO NARRADOR")[1]
    assert "unknown" in narrator_rules
    assert "Não chute" in narrator_rules


def test_json_example_carries_the_field_outside_classification():
    example = build_user_prompt("roteiro", {})
    assert '"narrator_gender"' in example
    # Antes de "classification" abrir: é campo de topo, como o hook.
    assert example.index('"narrator_gender"') < example.index('"classification"')


# --- endpoint ----------------------------------------------------------------


async def test_endpoint_returns_the_narrator_gender(client):
    payload = {**VALID_REFINE_RESPONSE, "narrator_gender": "male"}
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(return_value=RefineResponse.model_validate(payload))

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client):
        resp = await client.post("/refine", json={"script": "Eu estava sozinho naquele dia."})

    assert resp.status_code == 200
    assert resp.json()["narrator_gender"] == "male"


async def test_endpoint_reports_unknown_when_the_model_says_nothing(client):
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(
        return_value=RefineResponse.model_validate(VALID_REFINE_RESPONSE)
    )

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client):
        resp = await client.post("/refine", json={"script": "Texto qualquer."})

    assert resp.json()["narrator_gender"] == "unknown"
