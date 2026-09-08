import pytest

from src.content_scout.filters import (
    content_fingerprint,
    evaluate,
    normalize_for_fingerprint,
)
from src.content_scout.sources.base import Candidate

pytestmark = pytest.mark.no_db


def _candidate(text: str, title: str = "Título") -> Candidate:
    return Candidate(
        source="reddit",
        external_id="t3_x",
        origin="r/desabafos",
        title=title,
        text=text,
        url="https://reddit.com/x",
    )


def test_passes_when_above_the_floor():
    assert evaluate(_candidate("a" * 1000), min_chars=600) is None


def test_rejects_too_short():
    assert evaluate(_candidate("a" * 100), min_chars=600) == "too_short:100"


def test_floor_is_inclusive():
    assert evaluate(_candidate("a" * 600), min_chars=600) is None


def test_there_is_no_length_ceiling():
    """A long post is never rejected for being long — only condensed harder.

    There used to be a ceiling here; it was dropped once the refiner started
    always recounting every candidate, of any length, into the same short
    video. Length now only changes how much the refiner has to condense.
    """
    assert evaluate(_candidate("a" * 40000), min_chars=600) is None


def test_sensitive_wording_is_not_judged_here():
    """Safety moved to the moderation model — this function must not second-guess it.

    A substring blocklist used to reject "3 mil que não me mataria", a figure of
    speech about money, discarding a perfectly good story.
    """
    text = "Eram 3 mil que não me mataria, mas afundaria minhas contas. " + "a" * 600
    assert evaluate(_candidate(text), min_chars=600) is None


# ---------------------------------------------------------------- fingerprints


def test_fingerprint_is_stable_for_identical_text():
    assert content_fingerprint("Minha mãe fez isso.") == content_fingerprint("Minha mãe fez isso.")


def test_fingerprint_survives_reformatting():
    """A repost is retyped: different spacing, casing and punctuation, same story."""
    original = "Minha mãe fez isso.\n\nDepois ela se arrependeu!"
    repost = "minha mae   fez isso... Depois, ela se arrependeu"
    assert content_fingerprint(original) == content_fingerprint(repost)


def test_fingerprint_differs_for_different_stories():
    assert content_fingerprint("Minha mãe fez isso") != content_fingerprint("Meu pai fez aquilo")


def test_fingerprint_ignores_trailing_edits():
    """Reposts and edits pile up after the story — only the opening is hashed."""
    story = "Essa é a história toda. " * 60  # comfortably past FINGERPRINT_CHARS
    assert content_fingerprint(story) == content_fingerprint(story + "\n\nEDIT: obrigado pelos prêmios!")


def test_fingerprint_of_bodyless_text_is_none():
    """Empty must not hash — every such candidate would collide with every other,
    and the second would be discarded as a repost of the first."""
    assert content_fingerprint("") is None
    assert content_fingerprint("   \n\n  ") is None
    assert content_fingerprint("!!! ---- ???") is None


def test_normalize_keeps_digits():
    """Numbers carry the story ('R$ 3 mil'); dropping them would collide two
    otherwise-identical reports of different amounts."""
    assert normalize_for_fingerprint("R$ 3.000 sumiram") == "r3000sumiram"
