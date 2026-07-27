import pytest

from src.content_scout.filters import evaluate
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


def test_passes_when_within_bounds():
    assert evaluate(_candidate("a" * 1000), min_chars=600, max_chars=6000) is None


def test_rejects_too_short():
    assert evaluate(_candidate("a" * 100), min_chars=600, max_chars=6000) == "too_short:100"


def test_rejects_too_long():
    assert evaluate(_candidate("a" * 9000), min_chars=600, max_chars=6000) == "too_long:9000"


def test_boundaries_are_inclusive():
    assert evaluate(_candidate("a" * 600), min_chars=600, max_chars=6000) is None
    assert evaluate(_candidate("a" * 6000), min_chars=600, max_chars=6000) is None


def test_sensitive_wording_is_not_judged_here():
    """Safety moved to the moderation model — this function must not second-guess it.

    A substring blocklist used to reject "3 mil que não me mataria", a figure of
    speech about money, discarding a perfectly good story.
    """
    text = "Eram 3 mil que não me mataria, mas afundaria minhas contas. " + "a" * 600
    assert evaluate(_candidate(text), min_chars=600, max_chars=6000) is None
