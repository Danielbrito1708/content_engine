import pytest

from src.content_scout.filters import evaluate, find_blocked_term
from src.content_scout.sources.base import Candidate

pytestmark = pytest.mark.no_db

BLOCKLIST = ["suicíd", "estupr", "abuso sexual"]


def _candidate(text: str, title: str = "Título") -> Candidate:
    return Candidate(
        source="reddit",
        external_id="t3_x",
        origin="r/desabafos",
        title=title,
        text=text,
        url="https://reddit.com/x",
    )


def test_passes_when_within_bounds_and_clean():
    c = _candidate("a" * 1000)
    assert evaluate(c, min_chars=600, max_chars=6000, blocklist=BLOCKLIST) is None


def test_rejects_too_short():
    c = _candidate("a" * 100)
    assert evaluate(c, min_chars=600, max_chars=6000, blocklist=BLOCKLIST) == "too_short:100"


def test_rejects_too_long():
    c = _candidate("a" * 9000)
    assert evaluate(c, min_chars=600, max_chars=6000, blocklist=BLOCKLIST) == "too_long:9000"


def test_blocked_term_wins_over_length():
    """A blocked term is disqualifying regardless of how good the length is."""
    c = _candidate("suicídio " + "a" * 1000)
    assert evaluate(c, min_chars=600, max_chars=6000, blocklist=BLOCKLIST).startswith("blocked_term:")


def test_blocklist_ignores_accents_and_case():
    assert find_blocked_term(_candidate("pensei em SUICIDIO ontem"), BLOCKLIST) == "suicíd"
    assert find_blocked_term(_candidate("pensei em suicídio"), BLOCKLIST) == "suicíd"


def test_blocklist_matches_inflections_via_substring():
    assert find_blocked_term(_candidate("ele foi estuprada"), BLOCKLIST) == "estupr"


def test_blocklist_scans_the_title_too():
    c = _candidate("texto inofensivo", title="Sobre abuso sexual na família")
    assert find_blocked_term(c, BLOCKLIST) == "abuso sexual"


def test_clean_text_has_no_blocked_term():
    assert find_blocked_term(_candidate("história normal de término"), BLOCKLIST) is None


def test_empty_blocklist_blocks_nothing():
    assert find_blocked_term(_candidate("suicídio"), []) is None


def test_blank_entries_in_blocklist_are_ignored():
    """A trailing comma in config.ini must not turn into a match-everything rule."""
    assert find_blocked_term(_candidate("qualquer coisa"), ["", "  "]) is None
