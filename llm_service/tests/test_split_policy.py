"""O formato do produto: uma história completa, inteira, em 10 a 40 segundos.

Substituiu a política de divisão (história longa cortada em partes de até 30
minutos). As duas regras moram no prompt — quem reconta é o modelo —, então o
que dá para fixar em teste é que o prompt continua dizendo isso, que a banda em
palavras deriva de segundos e do `NARRATION_WPM` em vez de ser número solto, e
que a guarda do schema devolve uma parte só mesmo quando o modelo desobedece.
"""

import pytest

from src.llm_service.prompts.refine import (
    NARRATION_WPM,
    SYSTEM_PROMPT,
    TARGET_MAX_SECONDS,
    TARGET_MAX_WORDS,
    TARGET_MIN_SECONDS,
    TARGET_MIN_WORDS,
    build_user_prompt,
)
from src.llm_service.schemas.refine import RefineResponse

CLASSIFICATION = {
    "content_type": "drama",
    "tone": "emotional",
    "target_audience": {"age_range": [18, 35], "gender": "female", "interests": ["drama"]},
    "cta_per_part": ["devo me separar?"],
    "hashtag_hints": ["#drama"],
}


def _response(parts: list[str]) -> RefineResponse:
    return RefineResponse(parts=parts, classification=CLASSIFICATION)


def test_target_band_is_ten_to_forty_seconds():
    assert (TARGET_MIN_SECONDS, TARGET_MAX_SECONDS) == (10, 40)


def test_word_band_is_derived_from_seconds_and_wpm():
    assert TARGET_MIN_WORDS == TARGET_MIN_SECONDS * NARRATION_WPM // 60
    assert TARGET_MAX_WORDS == TARGET_MAX_SECONDS * NARRATION_WPM // 60


def test_wpm_matches_the_published_narration_rate():
    """150 wpm da voz neural pt-BR acelerados pelo `+50%` do template."""
    assert NARRATION_WPM == 225


def test_word_band_is_a_short_form_script():
    """O formato antigo permitia 5850 palavras (~30 min)."""
    assert TARGET_MAX_WORDS < 200


def test_prompt_states_the_band_in_both_units():
    assert str(TARGET_MIN_WORDS) in SYSTEM_PROMPT
    assert str(TARGET_MAX_WORDS) in SYSTEM_PROMPT
    assert f"{TARGET_MIN_SECONDS} a {TARGET_MAX_SECONDS} segundos" in SYSTEM_PROMPT


def test_prompt_orders_a_single_part_unconditionally():
    """Sem 'salvo se', sem teto acima do qual dividir volta a ser permitido."""
    assert "SEMPRE uma parte só" in SYSTEM_PROMPT
    assert "Nunca divida" in SYSTEM_PROMPT


def test_prompt_asks_to_condense_rather_than_preserve():
    """A regra anterior era o oposto: 'Não resuma, não encurte'."""
    assert "RECONTAR a história condensada" in SYSTEM_PROMPT
    assert "Não resuma" not in SYSTEM_PROMPT


def test_prompt_no_longer_carries_the_thirty_minute_ceiling():
    assert "30 minutos" not in SYSTEM_PROMPT
    assert "5850" not in SYSTEM_PROMPT


def test_prompt_requires_the_story_to_stand_alone():
    assert "COMPLETA" in SYSTEM_PROMPT


def test_user_prompt_example_shows_one_condensed_part():
    prompt = build_user_prompt("Era uma vez.", {})
    assert "uma única parte" in prompt
    assert "parte 2" not in prompt


def test_single_part_passes_through_untouched():
    assert _response(["Meu marido sumiu com a reserva. Devo me separar?"]).parts == [
        "Meu marido sumiu com a reserva. Devo me separar?"
    ]


def test_split_response_is_collapsed_into_one_part():
    """O modelo desobedeceu; o pipeline não pode virar série por causa disso."""
    assert _response(["Primeira metade.", "Segunda metade."]).parts == [
        "Primeira metade. Segunda metade."
    ]


def test_collapsing_keeps_the_ending_rather_than_dropping_it():
    """Juntar, nunca descartar: vídeo longo é ruim, vídeo sem fim é quebrado."""
    parts = _response(["Ele me traiu.", "Descobri que era com minha irmã. Devo contar?"]).parts
    assert "Devo contar?" in parts[0]


def test_blank_parts_are_dropped_before_collapsing():
    assert _response(["Só esta.", "   "]).parts == ["Só esta."]


@pytest.mark.parametrize("parts", [["Uma.", "Duas.", "Três."], ["A.", "B.", "C.", "D."]])
def test_any_number_of_parts_collapses_to_one(parts):
    assert len(_response(parts).parts) == 1


def test_hook_still_derives_from_the_collapsed_opening():
    """A junção não pode deslocar o gancho para o meio do texto."""
    assert _response(["Meu marido sumiu. Sobrou eu.", "E agora?"]).hook == "Meu marido sumiu."
