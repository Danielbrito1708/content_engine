"""Detecção de que a parte já abre pela frase gancho.

Puro — sem DB, sem rede. O que este módulo decide é se o vídeo toca o áudio do
gancho na frente da narração ou deixa a própria narração dizer a frase.
"""
import unicodedata

from src.orchestrator.hook_text import opens_with_hook

HOOK = "Ela achou a mensagem às 3 da manhã."
REST = "O celular estava na mesa, desbloqueado. Ela nem procurou."


def test_script_that_starts_with_the_hook():
    assert opens_with_hook(f"{HOOK} {REST}", HOOK) is True


def test_whitespace_differences_do_not_matter():
    assert opens_with_hook("Ela  achou a mensagem\nàs 3 da manhã.\n\n" + REST, HOOK) is True


def test_leading_whitespace_in_the_script_does_not_matter():
    assert opens_with_hook(f"\n  {HOOK} {REST}", HOOK) is True


def test_a_differently_composed_accent_still_matches():
    """`manhã` em NFD se lê igual e não é a mesma string."""
    script = unicodedata.normalize("NFD", f"{HOOK} {REST}")
    assert script != f"{HOOK} {REST}"  # a premissa do teste
    assert opens_with_hook(script, HOOK) is True


def test_case_difference_does_not_matter():
    assert opens_with_hook(f"ELA ACHOU A MENSAGEM ÀS 3 DA MANHÃ. {REST}", HOOK) is True


def test_part_that_is_only_the_hook_opens_with_it():
    assert opens_with_hook(HOOK, HOOK) is True


def test_hook_somewhere_else_in_the_script_is_not_an_opening():
    """Modelo que não copiou a frase literalmente: o vídeo abre com o áudio próprio."""
    assert opens_with_hook(f"{REST} {HOOK}", HOOK) is False


def test_a_different_sentence_is_not_an_opening():
    assert opens_with_hook("Ela achou a mensagem às 3 da tarde. " + REST, HOOK) is False


def test_script_that_ends_inside_the_hook_is_not_an_opening():
    assert opens_with_hook("Ela achou a mensagem", HOOK) is False


def test_empty_hook_is_never_an_opening():
    assert opens_with_hook(REST, "") is False
    assert opens_with_hook(REST, "   ") is False


def test_empty_script_is_never_an_opening():
    assert opens_with_hook("", HOOK) is False


def test_longer_script_beyond_the_hook_does_not_matter():
    """Só o prefixo é comparado — o resto do roteiro pode ser qualquer coisa."""
    assert opens_with_hook(HOOK + " — e o resto — " + REST * 3, HOOK) is True
