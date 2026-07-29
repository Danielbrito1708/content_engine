"""A política de divisão do roteiro é o formato do produto.

A história completa vai num vídeo só; dividir é a exceção, e só quando a
narração passaria de `MAX_PART_MINUTES`. A regra mora no prompt — o modelo é
quem corta —, então o que dá para fixar em teste é que o prompt continua
dizendo isso, e que o teto em palavras deriva de minutos e não de um número
solto que alguém mexeu sem perceber.
"""

from src.llm_service.prompts.refine import (
    MAX_PART_MINUTES,
    MAX_PART_WORDS,
    NARRATION_WPM,
    SYSTEM_PROMPT,
    build_user_prompt,
)


def test_ceiling_is_thirty_minutes():
    assert MAX_PART_MINUTES == 30


def test_word_ceiling_is_derived_from_minutes():
    assert MAX_PART_WORDS == MAX_PART_MINUTES * NARRATION_WPM


def test_word_ceiling_is_far_above_the_old_one_minute_format():
    """O formato antigo cortava em 600 palavras (~1 min de fala)."""
    assert MAX_PART_WORDS > 600 * 5


def test_prompt_defaults_to_a_single_part():
    assert "NÃO divida o roteiro" in SYSTEM_PROMPT
    assert "única parte" in SYSTEM_PROMPT


def test_prompt_states_the_ceiling_in_both_units():
    assert str(MAX_PART_WORDS) in SYSTEM_PROMPT
    assert f"~{MAX_PART_MINUTES} minutos" in SYSTEM_PROMPT


def test_prompt_forbids_shortening_the_story_to_fit():
    """Sem isso o modelo "resolve" o roteiro longo resumindo em vez de manter."""
    assert "Não resuma" in SYSTEM_PROMPT


def test_prompt_no_longer_carries_the_old_600_word_rule():
    assert "600 palavras" not in SYSTEM_PROMPT


def test_user_prompt_example_shows_one_part():
    prompt = build_user_prompt("Era uma vez.", {})
    assert '"parts": ["texto completo da história' in prompt
    assert "parte 2" not in prompt
