"""Corte da frase gancho do início da parte 1.

Puro — sem DB, sem rede. O que este módulo decide é se o vídeo diz a frase de
abertura uma vez ou duas.
"""
from src.orchestrator.hook_text import strip_hook

HOOK = "Ela achou a mensagem às 3 da manhã."
REST = "O celular estava na mesa, desbloqueado. Ela nem procurou."


def test_cuts_the_hook_from_the_start():
    assert strip_hook(f"{HOOK} {REST}", HOOK) == REST


def test_cut_survives_different_whitespace():
    # Quebra de linha entre a frase gancho e o resto, e espaço duplo dentro dela.
    script = "Ela  achou a mensagem\nàs 3 da manhã.\n\n" + REST
    assert strip_hook(script, HOOK) == REST


def test_cut_survives_leading_whitespace_in_the_script():
    assert strip_hook(f"\n  {HOOK} {REST}", HOOK) == REST


def test_cut_survives_a_differently_composed_accent():
    """`manhã` em NFD se lê igual e não é a mesma string; o corte sai em NFC."""
    import unicodedata

    script = unicodedata.normalize("NFD", f"{HOOK} {REST}")
    assert script != f"{HOOK} {REST}"  # a premissa do teste
    assert strip_hook(script, HOOK) == REST


def test_case_difference_does_not_block_the_cut():
    assert strip_hook(f"ELA ACHOU A MENSAGEM ÀS 3 DA MANHÃ. {REST}", HOOK) == REST


def test_hook_that_is_not_the_opening_is_not_cut():
    """Modelo que não copiou a frase literalmente: melhor repetir do que cortar errado."""
    assert strip_hook(f"{REST} {HOOK}", HOOK) is None


def test_partial_match_is_not_cut():
    assert strip_hook("Ela achou a mensagem às 3 da tarde. " + REST, HOOK) is None


def test_script_that_ends_inside_the_hook_is_not_cut():
    assert strip_hook("Ela achou a mensagem", HOOK) is None


def test_script_that_is_only_the_hook_is_not_cut():
    """Cortar deixaria a parte sem texto nenhum para narrar."""
    assert strip_hook(HOOK, HOOK) is None
    assert strip_hook(f"{HOOK}   \n", HOOK) is None


def test_empty_hook_is_not_cut():
    assert strip_hook(REST, "") is None
    assert strip_hook(REST, "   ") is None


def test_empty_script_is_not_cut():
    assert strip_hook("", HOOK) is None


def test_only_the_prefix_is_rewritten():
    """O resto do roteiro sai intacto, com a pontuação e o espaçamento originais."""
    tail = "  Duas   linhas\n\ne um travessão — assim.  "
    assert strip_hook(HOOK + tail, HOOK) == tail.lstrip()
