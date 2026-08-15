"""O título do vídeo no YouTube.

O TikTok não tem título — só legenda —, então até aqui o refino não produzia
nenhum. Publicar o mesmo vídeo no YouTube exige um: o Buffer recusa a criação
do post sem ele. É campo próprio, e não o gancho reaproveitado, porque os dois
textos são lidos em momentos diferentes: o gancho é a primeira coisa OUVIDA
depois que o vídeo abre, o título é a única coisa LIDA antes de ele abrir.
"""
from unittest.mock import AsyncMock, patch

from src.llm_service.prompts.refine import SYSTEM_PROMPT, build_user_prompt
from src.llm_service.schemas.refine import (
    MAX_TITLE_CHARS,
    RefineResponse,
    derive_youtube_title,
    truncate_title,
)

from tests.conftest import VALID_REFINE_RESPONSE


# ── truncate_title ─────────────────────────────────────────────────────────

def test_truncate_title_keeps_short_title_intact():
    assert truncate_title("Meu chefe me demitiu por e-mail") == "Meu chefe me demitiu por e-mail"


def test_truncate_title_collapses_whitespace():
    assert truncate_title("  Ela   descobriu\ntudo  ") == "Ela descobriu tudo"


def test_truncate_title_does_not_split_a_word():
    long = "palavra " * 30
    result = truncate_title(long)
    assert len(result) <= MAX_TITLE_CHARS
    assert result.endswith("palavra")


def test_truncate_title_drops_dangling_punctuation():
    """Corte no meio de uma enumeração não pode deixar o título terminando em vírgula."""
    title = "a" * 90 + " bbb, ccccccccccc"
    assert truncate_title(title) == "a" * 90 + " bbb"


def test_truncate_title_cuts_a_single_oversized_word():
    """Sem espaço para cortar, corte seco — título vazio faria o Buffer recusar o post."""
    result = truncate_title("x" * 150)
    assert result == "x" * MAX_TITLE_CHARS


# ── derive_youtube_title ───────────────────────────────────────────────────

def test_derive_falls_back_to_the_hook():
    assert derive_youtube_title("Ela achou as mensagens.", ["texto"]) == "Ela achou as mensagens."


def test_derive_falls_back_to_the_script_when_there_is_no_hook():
    assert derive_youtube_title("", ["Ele sumiu com o carro. Depois ligou."]) == "Ele sumiu com o carro."


def test_derive_truncates_a_long_hook():
    hook = "Ela descobriu " + "uma coisa muito estranha " * 10
    assert len(derive_youtube_title(hook, [])) <= MAX_TITLE_CHARS


def test_derive_returns_empty_without_hook_or_parts():
    assert derive_youtube_title("", []) == ""


# ── RefineResponse ─────────────────────────────────────────────────────────

def test_model_keeps_the_title_it_was_given():
    data = {**VALID_REFINE_RESPONSE, "youtube_title": "O e-mail que meu chefe não devia ter mandado"}
    assert RefineResponse.model_validate(data).youtube_title == (
        "O e-mail que meu chefe não devia ter mandado"
    )


def test_model_truncates_a_title_over_the_ceiling():
    data = {**VALID_REFINE_RESPONSE, "youtube_title": "Muito longo " * 20}
    assert len(RefineResponse.model_validate(data).youtube_title) <= MAX_TITLE_CHARS


def test_model_derives_the_title_when_the_field_is_missing():
    """Modelo que ignora o campo novo não pode produzir vídeo sem título."""
    parsed = RefineResponse.model_validate(VALID_REFINE_RESPONSE)
    assert parsed.youtube_title
    assert len(parsed.youtube_title) <= MAX_TITLE_CHARS


def test_model_derives_the_title_from_the_filled_hook_not_the_raw_field():
    """`_fill_youtube_title` roda depois de `_fill_hook` — invertê-los daria título vazio."""
    data = {**VALID_REFINE_RESPONSE, "hook": "", "youtube_title": ""}
    parsed = RefineResponse.model_validate(data)
    assert parsed.youtube_title == truncate_title(parsed.hook)


def test_model_ignores_a_whitespace_only_title():
    data = {**VALID_REFINE_RESPONSE, "youtube_title": "   "}
    assert RefineResponse.model_validate(data).youtube_title


# ── prompt ─────────────────────────────────────────────────────────────────

def test_prompt_asks_for_the_field_and_states_the_ceiling():
    assert "youtube_title" in SYSTEM_PROMPT
    assert str(MAX_TITLE_CHARS) in SYSTEM_PROMPT


def test_prompt_forbids_copying_the_hook_and_the_part_label():
    assert "NÃO é o gancho copiado" in SYSTEM_PROMPT
    assert "(Parte 1/2)" in SYSTEM_PROMPT


def test_user_prompt_json_example_carries_the_field():
    assert "youtube_title" in build_user_prompt("roteiro", {})


# ── rota ───────────────────────────────────────────────────────────────────

async def test_refine_route_returns_the_title(client):
    parsed = RefineResponse.model_validate(
        {**VALID_REFINE_RESPONSE, "youtube_title": "O que ela achou no celular dele"}
    )
    with patch("src.llm_service.api.routes.refine.get_llm_client") as factory:
        factory.return_value.refine = AsyncMock(return_value=parsed)
        resp = await client.post("/refine", json={"script": "roteiro bruto", "metadata": {}})

    assert resp.status_code == 200
    assert resp.json()["youtube_title"] == "O que ela achou no celular dele"
