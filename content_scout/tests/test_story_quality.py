import pytest
import respx
from httpx import Response

from src.content_scout.clients.llm import (
    TAG_NO_HOOK,
    TAG_STRONG,
    TAG_WEAK,
    StoryQualityClient,
    StoryScore,
)
from src.content_scout.scout import interleave_by_origin, rank_by_story
from src.content_scout.sources.base import Candidate

pytestmark = pytest.mark.no_db

URL = "http://llm_service:8000/story-quality"


def _candidate(external_id: str, origin: str = "r/a", chars: int = 1000) -> Candidate:
    return Candidate(
        source="reddit",
        external_id=external_id,
        origin=origin,
        title=f"título {external_id}",
        text="a" * chars,
        url=f"https://reddit.com/{external_id}",
    )


def _verdict(index: int, hook: bool = True, score: int = 7, **extra) -> dict:
    return {"index": index, "hook": hook, "score": score, **extra}


# ─────────────────────────── tagging ───────────────────────────


def test_strong_story_with_hook_is_tagged_strong():
    assert StoryScore(hook=True, score=8).tag(min_score=6) == TAG_STRONG


def test_score_below_threshold_is_tagged_weak():
    assert StoryScore(hook=True, score=5).tag(min_score=6) == TAG_WEAK


def test_score_at_threshold_is_not_weak():
    assert StoryScore(hook=True, score=6).tag(min_score=6) == TAG_STRONG


def test_good_story_without_hook_is_tagged_no_hook():
    """A buried lede is a different problem from a bad story."""
    assert StoryScore(hook=False, score=9).tag(min_score=6) == TAG_NO_HOOK


def test_weak_outranks_no_hook():
    """A weak story is the headline even when the title happens to hook."""
    assert StoryScore(hook=False, score=2).tag(min_score=6) == TAG_WEAK


def test_metadata_carries_the_hook_line_when_there_is_one():
    meta = StoryScore(hook=True, score=8, hook_line="ela se vingou").as_metadata(min_score=6)

    assert meta == {
        "story_tag": TAG_STRONG,
        "story_score": 8,
        "has_hook": True,
        "has_villain": False,
        "hook_line": "ela se vingou",
    }


def test_metadata_omits_hook_line_when_absent():
    meta = StoryScore(hook=False, score=3).as_metadata(min_score=6)

    assert "hook_line" not in meta
    assert meta["story_tag"] == TAG_WEAK


# ─────────────────────────── ranking ───────────────────────────


def test_rank_puts_the_strongest_opening_first():
    candidates = [_candidate("a"), _candidate("b"), _candidate("c")]
    scores = {
        "a": StoryScore(hook=False, score=3),
        "b": StoryScore(hook=True, score=9),
        "c": StoryScore(hook=True, score=6),
    }

    ordered = rank_by_story(candidates, scores, neutral=6, outrage_neutral=6, outrage_weight=2)

    assert [c.external_id for c in ordered] == ["b", "c", "a"]


def test_rank_keeps_source_order_on_ties():
    """Feed position is the tiebreaker — it is the only other signal there is."""
    candidates = [_candidate(x) for x in ("a", "b", "c")]
    scores = {x: StoryScore(hook=True, score=7) for x in ("a", "b", "c")}

    ordered = rank_by_story(candidates, scores, neutral=6, outrage_neutral=6, outrage_weight=2)

    assert [c.external_id for c in ordered] == ["a", "b", "c"]


def test_unscored_candidate_sorts_at_the_threshold_not_last():
    """A model failure must not permanently handicap an unjudged story."""
    candidates = [_candidate(x) for x in ("weak", "unscored", "strong")]
    scores = {
        "weak": StoryScore(hook=False, score=2),
        "strong": StoryScore(hook=True, score=9),
    }

    ordered = rank_by_story(candidates, scores, neutral=6, outrage_neutral=6, outrage_weight=2)

    assert [c.external_id for c in ordered] == ["strong", "unscored", "weak"]


def test_rank_with_no_scores_at_all_is_a_no_op():
    """Total outage falls straight back to the source's own ranking."""
    candidates = [_candidate(x) for x in ("a", "b", "c")]

    ordered = rank_by_story(candidates, {}, neutral=6, outrage_neutral=6, outrage_weight=2)

    assert [c.external_id for c in ordered] == ["a", "b", "c"]


def test_rank_empty():
    assert rank_by_story([], {}, neutral=6, outrage_neutral=6, outrage_weight=2) == []


def test_ranking_survives_the_interleave_without_breaking_fairness():
    """Score orders within an origin; the interleave still alternates between them."""
    candidates = [
        _candidate("a_bad", origin="r/a"),
        _candidate("a_good", origin="r/a"),
        _candidate("b_bad", origin="r/b"),
        _candidate("b_good", origin="r/b"),
    ]
    scores = {
        "a_bad": StoryScore(hook=False, score=2),
        "a_good": StoryScore(hook=True, score=9),
        "b_bad": StoryScore(hook=False, score=3),
        "b_good": StoryScore(hook=True, score=8),
    }

    ordered = interleave_by_origin(rank_by_story(candidates, scores, neutral=6, outrage_neutral=6, outrage_weight=2))

    assert [c.external_id for c in ordered] == ["a_good", "b_good", "a_bad", "b_bad"]


# ─────────────────────────── the client ───────────────────────────


def test_payload_indexes_by_position_and_truncates_the_body(monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings.CONFIG.scout, "story_excerpt_chars", 50)
    payload = StoryQualityClient()._payload([_candidate("a", chars=500), _candidate("b")])

    assert [item["index"] for item in payload["items"]] == [0, 1]
    assert payload["items"][0]["title"] == "título a"
    assert len(payload["items"][0]["opening"]) == 50


async def test_empty_candidate_list_makes_no_call():
    """Nothing to judge means nothing to pay for."""
    with respx.mock:
        route = respx.post(URL).mock(return_value=Response(200, json={"results": []}))
        assert await StoryQualityClient().score([]) == {}
        assert not route.called


@respx.mock
async def test_verdicts_are_keyed_by_external_id():
    respx.post(URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    _verdict(0, score=9, hook_line="ela se vingou", reason="ótimo gancho"),
                    _verdict(1, hook=False, score=2),
                ]
            },
        )
    )

    scores = await StoryQualityClient().score([_candidate("t3_a"), _candidate("t3_b")])

    assert set(scores) == {"t3_a", "t3_b"}
    assert scores["t3_a"] == StoryScore(
        hook=True, score=9, hook_line="ela se vingou", reason="ótimo gancho"
    )
    assert scores["t3_b"] == StoryScore(hook=False, score=2)


@respx.mock
async def test_out_of_order_verdicts_land_on_the_right_candidate():
    """The index maps to a position, so the model's ordering cannot shift a score."""
    respx.post(URL).mock(
        return_value=Response(200, json={"results": [_verdict(1, score=3), _verdict(0, score=8)]})
    )

    scores = await StoryQualityClient().score([_candidate("t3_a"), _candidate("t3_b")])

    assert scores["t3_a"].score == 8
    assert scores["t3_b"].score == 3


@respx.mock
async def test_unanswered_candidate_is_absent_not_zero():
    """Missing means "not judged" — the same distinction as comment_count IS NULL."""
    respx.post(URL).mock(return_value=Response(200, json={"results": [_verdict(0)]}))

    scores = await StoryQualityClient().score([_candidate("t3_a"), _candidate("t3_b")])

    assert "t3_b" not in scores


@respx.mock
async def test_out_of_range_index_is_ignored():
    respx.post(URL).mock(
        return_value=Response(200, json={"results": [_verdict(0), _verdict(9)]})
    )

    scores = await StoryQualityClient().score([_candidate("t3_a")])

    assert set(scores) == {"t3_a"}


@respx.mock
async def test_verdict_without_a_score_is_dropped():
    respx.post(URL).mock(
        return_value=Response(200, json={"results": [{"index": 0, "hook": True}]})
    )

    assert await StoryQualityClient().score([_candidate("t3_a")]) == {}


@respx.mock
async def test_http_error_yields_no_scores_instead_of_raising():
    """This signal only orders candidates — losing it must not end the cycle."""
    respx.post(URL).mock(return_value=Response(502))

    assert await StoryQualityClient().score([_candidate("t3_a")]) == {}


@respx.mock
async def test_unparseable_body_yields_no_scores():
    respx.post(URL).mock(return_value=Response(200, text="não é json"))

    assert await StoryQualityClient().score([_candidate("t3_a")]) == {}


@respx.mock
async def test_missing_results_key_yields_no_scores():
    respx.post(URL).mock(return_value=Response(200, json={}))

    assert await StoryQualityClient().score([_candidate("t3_a")]) == {}


# ─────────────────────────── revolta ───────────────────────────


def test_outrage_outranks_a_better_told_calm_story():
    """The channel is built on indignation — that is what the ordering buys."""
    candidates = [_candidate(x) for x in ("calma", "revoltante")]
    scores = {
        "calma": StoryScore(hook=True, score=9, outrage=2),
        "revoltante": StoryScore(hook=True, score=6, outrage=9, villain=True),
    }

    ordered = rank_by_story(candidates, scores, neutral=6, outrage_neutral=6, outrage_weight=2)

    assert [c.external_id for c in ordered] == ["revoltante", "calma"]


def test_storytelling_still_wins_when_the_outrage_gap_is_small():
    """Weighting is not a priority order: 10/3 must not beat 9/9."""
    candidates = [_candidate(x) for x in ("mal_contada", "bem_contada")]
    scores = {
        "mal_contada": StoryScore(hook=True, score=3, outrage=10, villain=True),
        "bem_contada": StoryScore(hook=True, score=9, outrage=9, villain=True),
    }

    ordered = rank_by_story(candidates, scores, neutral=6, outrage_neutral=6, outrage_weight=2)

    assert [c.external_id for c in ordered] == ["bem_contada", "mal_contada"]


def test_weight_zero_reproduces_the_old_pure_storytelling_order():
    """The knob has to be able to turn the whole thing off."""
    candidates = [_candidate(x) for x in ("revoltante", "bem_contada")]
    scores = {
        "revoltante": StoryScore(hook=True, score=4, outrage=10),
        "bem_contada": StoryScore(hook=True, score=9, outrage=0),
    }

    ordered = rank_by_story(candidates, scores, neutral=6, outrage_neutral=6, outrage_weight=0)

    assert [c.external_id for c in ordered] == ["bem_contada", "revoltante"]


def test_unjudged_outrage_sorts_at_the_threshold_not_at_zero():
    """A model that skipped the field must not bury the story under a real 0."""
    candidates = [_candidate(x) for x in ("sem_revolta", "sem_campo")]
    scores = {
        "sem_revolta": StoryScore(hook=True, score=7, outrage=0),
        "sem_campo": StoryScore(hook=True, score=7),
    }

    ordered = rank_by_story(candidates, scores, neutral=6, outrage_neutral=6, outrage_weight=2)

    assert [c.external_id for c in ordered] == ["sem_campo", "sem_revolta"]


def test_unscored_candidate_still_sorts_at_both_thresholds():
    """Same rule as before, now across two axes instead of one."""
    candidates = [_candidate(x) for x in ("fraca", "nao_julgada", "forte")]
    scores = {
        "fraca": StoryScore(hook=False, score=2, outrage=1),
        "forte": StoryScore(hook=True, score=9, outrage=9),
    }

    ordered = rank_by_story(candidates, scores, neutral=6, outrage_neutral=6, outrage_weight=2)

    assert [c.external_id for c in ordered] == ["forte", "nao_julgada", "fraca"]


def test_rank_value_is_the_weighted_sum():
    assert StoryScore(hook=True, score=6, outrage=9).rank_value(
        outrage_neutral=6, outrage_weight=2
    ) == 24


def test_metadata_carries_outrage_and_villain():
    meta = StoryScore(hook=True, score=8, outrage=9, villain=True).as_metadata(min_score=6)

    assert meta["outrage_score"] == 9
    assert meta["has_villain"] is True


def test_metadata_omits_outrage_when_it_was_not_judged():
    """The refiner must not read "not judged" as "nothing to be angry about"."""
    meta = StoryScore(hook=True, score=8).as_metadata(min_score=6)

    assert "outrage_score" not in meta
    assert meta["has_villain"] is False


@respx.mock
async def test_client_parses_outrage_and_villain():
    respx.post(URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {"index": 0, "hook": True, "score": 7, "outrage": 9, "villain": True}
                ]
            },
        )
    )

    scores = await StoryQualityClient().score([_candidate("a")])

    assert scores["a"].outrage == 9
    assert scores["a"].villain is True


@respx.mock
async def test_client_keeps_the_verdict_when_outrage_is_missing():
    """Outrage degrades on its own — the storytelling verdict is still usable."""
    respx.post(URL).mock(
        return_value=Response(200, json={"results": [_verdict(0, score=8)]})
    )

    scores = await StoryQualityClient().score([_candidate("a")])

    assert scores["a"].score == 8
    assert scores["a"].outrage is None
    assert scores["a"].villain is False


@respx.mock
async def test_client_treats_garbage_outrage_as_unjudged():
    respx.post(URL).mock(
        return_value=Response(
            200, json={"results": [_verdict(0, score=8, outrage="muito")]}
        )
    )

    scores = await StoryQualityClient().score([_candidate("a")])

    assert scores["a"].outrage is None
