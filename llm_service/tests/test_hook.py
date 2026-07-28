"""A frase gancho do roteiro.

O gancho existia como regra de escrita ("a primeira frase deve prender") mas não
como campo. Passou a existir porque o pipeline narra essa frase num arquivo
separado — o que exige saber onde ela termina, e isso o texto corrido não diz.
"""
from unittest.mock import AsyncMock, patch

from src.llm_service.schemas.refine import MAX_HOOK_CHARS, RefineResponse, derive_hook

from tests.conftest import VALID_REFINE_RESPONSE


# ── derive_hook ────────────────────────────────────────────────────────────

def test_derive_hook_takes_first_sentence():
    assert derive_hook(["Ele sumiu com o carro. Depois ligou chorando."]) == "Ele sumiu com o carro."


def test_derive_hook_handles_ellipsis_and_bang():
    assert derive_hook(["Ela não sabia... Mas eu sabia."]) == "Ela não sabia..."
    assert derive_hook(["Eu perdi tudo! E ainda agradeci."]) == "Eu perdi tudo!"


def test_derive_hook_keeps_closing_quote():
    assert derive_hook(['Ele disse "acabou." Aí desligou.']) == 'Ele disse "acabou."'


def test_derive_hook_does_not_split_decimal():
    """`3.5` não termina frase — o ponto não é seguido de espaço."""
    assert derive_hook(["Ele me devia R$ 3.5 mil e sumiu. Fim."]) == "Ele me devia R$ 3.5 mil e sumiu."


def test_derive_hook_returns_whole_text_when_single_sentence():
    assert derive_hook(["Só uma frase sem ponto final"]) == "Só uma frase sem ponto final"


def test_derive_hook_truncates_at_word_boundary():
    long_part = "palavra " * 60  # 480 chars, sem pontuação terminal
    hook = derive_hook([long_part])

    assert len(hook) <= MAX_HOOK_CHARS
    assert not hook.endswith("palav")  # não corta no meio da palavra
    assert hook.endswith("palavra")


def test_derive_hook_uses_only_first_part():
    assert derive_hook(["Parte um.", "Parte dois."]) == "Parte um."


def test_derive_hook_empty_input():
    assert derive_hook([]) == ""
    assert derive_hook(["   "]) == ""


# ── RefineResponse ─────────────────────────────────────────────────────────

def test_hook_from_model_is_preserved():
    data = {**VALID_REFINE_RESPONSE, "hook": "Ela achou a mensagem às 3 da manhã."}
    assert RefineResponse.model_validate(data).hook == "Ela achou a mensagem às 3 da manhã."


def test_hook_is_derived_when_model_omits_it():
    """Contrato não pode depender de o modelo obedecer o prompt."""
    resp = RefineResponse.model_validate(VALID_REFINE_RESPONSE)

    assert resp.hook == "Você não vai acreditar no que ela descobriu no celular dele..."


def test_hook_is_derived_when_model_returns_blank():
    resp = RefineResponse.model_validate({**VALID_REFINE_RESPONSE, "hook": "   "})

    assert resp.hook == "Você não vai acreditar no que ela descobriu no celular dele..."


def test_hook_is_stripped():
    resp = RefineResponse.model_validate({**VALID_REFINE_RESPONSE, "hook": "  Gancho.  "})

    assert resp.hook == "Gancho."


# ── endpoint ───────────────────────────────────────────────────────────────

async def test_refine_returns_hook(client):
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(
        return_value=RefineResponse.model_validate(
            {**VALID_REFINE_RESPONSE, "hook": "Ela achou a mensagem às 3 da manhã."}
        )
    )

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client):
        resp = await client.post("/refine", json={"script": "Roteiro."})

    assert resp.status_code == 200
    assert resp.json()["hook"] == "Ela achou a mensagem às 3 da manhã."


async def test_refine_response_always_has_hook_field(client):
    """Mesmo sem gancho do modelo, o campo vem preenchido — o orchestrador
    decide narrar ou não a partir dele."""
    mock_client = AsyncMock()
    mock_client.refine = AsyncMock(return_value=RefineResponse.model_validate(VALID_REFINE_RESPONSE))

    with patch("src.llm_service.api.routes.refine.get_llm_client", return_value=mock_client):
        resp = await client.post("/refine", json={"script": "Roteiro."})

    assert resp.json()["hook"]


def test_prompt_asks_for_the_hook_field():
    from src.llm_service.prompts.refine import SYSTEM_PROMPT, build_user_prompt

    assert '"hook"' in SYSTEM_PROMPT
    assert '"hook"' in build_user_prompt("roteiro", {})
