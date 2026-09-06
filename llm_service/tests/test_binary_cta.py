"""O CTA de votação binária, na legenda do post.

É texto NOVO, diferente de `cta_per_part`: aquele é a pergunta que fecha o
texto narrado e nunca vai para a legenda (REGRA DO FECHAMENTO, ver
`test_refine.py`); este nasce só para quem lê a legenda antes de assistir, e
por isso pode citar o dilema sem citar o desfecho.
"""
from unittest.mock import AsyncMock, patch

from src.llm_service.prompts.refine import SYSTEM_PROMPT, build_user_prompt
from src.llm_service.schemas.refine import (
    MAX_BINARY_CTA_CHARS,
    Classification,
    RefineResponse,
    truncate_binary_cta,
)

from tests.conftest import VALID_REFINE_RESPONSE


# ── truncate_binary_cta ────────────────────────────────────────────────────

def test_truncate_binary_cta_keeps_short_text_intact():
    assert truncate_binary_cta("Quem errou mais: ele ou ela?") == "Quem errou mais: ele ou ela?"


def test_truncate_binary_cta_collapses_whitespace():
    assert truncate_binary_cta("  Comenta   1\nou 2  ") == "Comenta 1 ou 2"


def test_truncate_binary_cta_does_not_split_a_word():
    long = "palavra " * 30
    result = truncate_binary_cta(long)
    assert len(result) <= MAX_BINARY_CTA_CHARS
    assert result.endswith("palavra")


def test_truncate_binary_cta_cuts_a_single_oversized_word():
    result = truncate_binary_cta("x" * 150)
    assert result == "x" * MAX_BINARY_CTA_CHARS


# ── Classification ─────────────────────────────────────────────────────────

def test_classification_defaults_to_empty():
    """Sem dilema claro, o campo fica vazio — a legenda simplesmente não leva pergunta."""
    data = {**VALID_REFINE_RESPONSE["classification"]}
    data.pop("binary_cta", None)
    assert Classification.model_validate(data).binary_cta == ""


def test_classification_keeps_the_cta_it_was_given():
    data = {**VALID_REFINE_RESPONSE["classification"], "binary_cta": "Quem errou mais: o marido ou a sogra?"}
    assert Classification.model_validate(data).binary_cta == "Quem errou mais: o marido ou a sogra?"


def test_classification_truncates_a_cta_over_the_ceiling():
    data = {**VALID_REFINE_RESPONSE["classification"], "binary_cta": "Muito longo " * 20}
    assert len(Classification.model_validate(data).binary_cta) <= MAX_BINARY_CTA_CHARS


def test_classification_ignores_a_whitespace_only_cta():
    data = {**VALID_REFINE_RESPONSE["classification"], "binary_cta": "   "}
    assert Classification.model_validate(data).binary_cta == ""


def test_classification_binary_cta_is_independent_from_cta_per_part():
    data = {
        **VALID_REFINE_RESPONSE["classification"],
        "cta_per_part": ["devo me separar?"],
        "binary_cta": "quem errou mais: eu ou ele?",
    }
    parsed = Classification.model_validate(data)
    assert parsed.cta_per_part == ["devo me separar?"]
    assert parsed.binary_cta == "quem errou mais: eu ou ele?"


# ── prompt ──────────────────────────────────────────────────────────────────

def test_prompt_asks_for_the_field_and_states_the_ceiling():
    assert "binary_cta" in SYSTEM_PROMPT
    assert str(MAX_BINARY_CTA_CHARS) in SYSTEM_PROMPT


def test_prompt_distinguishes_it_from_cta_per_part():
    assert 'diferente do "cta_per_part"' in SYSTEM_PROMPT


def test_prompt_forbids_spoiling_the_ending():
    assert "não pode entregar como a história termina" in SYSTEM_PROMPT


def test_prompt_allows_leaving_it_empty():
    assert "deixe vazio" in SYSTEM_PROMPT


def test_user_prompt_json_example_carries_the_field():
    assert "binary_cta" in build_user_prompt("roteiro", {})


# ── rota ───────────────────────────────────────────────────────────────────

async def test_refine_route_returns_the_binary_cta(client):
    data = {**VALID_REFINE_RESPONSE}
    data["classification"] = {**data["classification"], "binary_cta": "Quem errou mais: ele ou ela?"}
    parsed = RefineResponse.model_validate(data)

    with patch("src.llm_service.api.routes.refine.get_llm_client") as factory:
        factory.return_value.refine = AsyncMock(return_value=parsed)
        resp = await client.post("/refine", json={"script": "roteiro bruto", "metadata": {}})

    assert resp.status_code == 200
    assert resp.json()["classification"]["binary_cta"] == "Quem errou mais: ele ou ela?"
