import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from src.content_scout.clients.llm import (
    TAG_NO_HOOK,
    TAG_STRONG,
    TAG_WEAK,
    ModerationClient,
    ModerationError,
    StoryQualityClient,
    StoryScore,
    Verdict,
)
from src.content_scout.clients.orchestrator import OrchestratorClient
from src.content_scout.db.models import SeenItem, SeenStatus
from src.content_scout.scout import interleave_by_origin, run_cycle
from src.content_scout.sources.base import Candidate, Comment, CommentThread
from src.core import settings


class FakeSource:
    name = "reddit"

    def __init__(self, candidates):
        self._candidates = candidates

    async def fetch(self):
        return list(self._candidates)


def _candidate(external_id: str, chars: int = 1000, title: str = "Título",
               origin: str = "r/desabafos") -> Candidate:
    return Candidate(
        source="reddit",
        external_id=external_id,
        origin=origin,
        title=title,
        text="a" * chars,
        url=f"https://reddit.com/{external_id}",
    )


def test_interleave_alternates_between_origins():
    ordered = interleave_by_origin([
        _candidate("a1", origin="r/a"), _candidate("a2", origin="r/a"),
        _candidate("b1", origin="r/b"), _candidate("b2", origin="r/b"),
    ])
    assert [c.external_id for c in ordered] == ["a1", "b1", "a2", "b2"]


def test_interleave_preserves_ranking_within_each_origin():
    """Position in the feed is the only quality signal — it must survive."""
    ordered = interleave_by_origin([
        _candidate(f"a{i}", origin="r/a") for i in range(3)
    ] + [_candidate("b0", origin="r/b")])
    per_origin = [c.external_id for c in ordered if c.origin == "r/a"]
    assert per_origin == ["a0", "a1", "a2"]


def test_interleave_handles_uneven_groups():
    ordered = interleave_by_origin([
        _candidate("a1", origin="r/a"), _candidate("a2", origin="r/a"),
        _candidate("a3", origin="r/a"), _candidate("b1", origin="r/b"),
    ])
    assert [c.external_id for c in ordered] == ["a1", "b1", "a2", "a3"]


def test_interleave_single_origin_is_unchanged():
    ids = ["a1", "a2", "a3"]
    ordered = interleave_by_origin([_candidate(i, origin="r/a") for i in ids])
    assert [c.external_id for c in ordered] == ids


def test_interleave_empty():
    assert interleave_by_origin([]) == []


@pytest.fixture
def submissions(monkeypatch):
    """Capture what the scout sends to the orchestrator, with no network."""
    sent = []

    async def fake_create(self, script, metadata):
        run_id = uuid.uuid4()
        sent.append({"script": script, "metadata": metadata, "run_id": run_id})
        return run_id

    monkeypatch.setattr(OrchestratorClient, "create_pipeline", fake_create)
    return sent


@pytest.fixture
def set_active_runs(monkeypatch):
    def _set(count):
        async def fake_count(self, sample=50):
            return count
        monkeypatch.setattr(OrchestratorClient, "count_active_runs", fake_count)
    _set(0)
    return _set


@pytest.fixture(autouse=True)
def moderation(monkeypatch):
    """Every candidate is safe unless a test says otherwise. No LLM is called."""
    calls: list[tuple[str, str]] = []
    behavior = {"fn": lambda title, text: Verdict(safe=True)}

    async def fake_check(self, title, text):
        calls.append((title, text))
        result = behavior["fn"](title, text)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ModerationClient, "check", fake_check)
    return SimpleNamespace(calls=calls, set=lambda fn: behavior.__setitem__("fn", fn))


@pytest.fixture(autouse=True)
def story_quality(monkeypatch):
    """No candidate is scored unless a test says so, and no LLM is called.

    Defaulting to "no scores" keeps every pre-existing test exercising the real
    code path for an unreachable ``llm_service`` — which is the case that must not
    change behaviour: candidates keep the source's ranking and still publish.
    """
    calls: list[list[str]] = []
    scores: dict[str, StoryScore] = {}

    async def fake_score(self, candidates):
        calls.append([c.external_id for c in candidates])
        return {c.external_id: scores[c.external_id] for c in candidates if c.external_id in scores}

    monkeypatch.setattr(StoryQualityClient, "score", fake_score)
    return SimpleNamespace(calls=calls, set=scores.update)


@pytest.fixture
def budget(monkeypatch):
    def _set(max_pending_runs=5, max_per_cycle=2):
        monkeypatch.setattr(settings.CONFIG.scout, "max_pending_runs", max_pending_runs)
        monkeypatch.setattr(settings.CONFIG.scout, "max_per_cycle", max_per_cycle)
    _set()
    return _set


async def _seen_rows(session):
    result = await session.execute(select(SeenItem).order_by(SeenItem.external_id))
    return result.scalars().all()


async def test_submits_fresh_candidate_and_records_it(submissions, set_active_runs, budget, session):
    source = FakeSource([_candidate("t3_a")])

    report = await run_cycle([source])

    assert report.fetched == 1
    assert report.submitted == 1
    assert report.submitted_ids == ["t3_a"]
    assert len(submissions) == 1

    rows = await _seen_rows(session)
    assert len(rows) == 1
    assert rows[0].status == SeenStatus.submitted
    assert rows[0].pipeline_run_id == submissions[0]["run_id"]


async def test_metadata_carries_provenance_to_orchestrator(submissions, set_active_runs, budget):
    await run_cycle([FakeSource([_candidate("t3_a")])])

    meta = submissions[0]["metadata"]
    assert meta["source"] == "reddit"
    assert meta["origin"] == "r/desabafos"
    assert meta["url"] == "https://reddit.com/t3_a"


async def test_already_seen_candidate_is_not_resubmitted(submissions, set_active_runs, budget):
    candidate = _candidate("t3_a")

    first = await run_cycle([FakeSource([candidate])])
    second = await run_cycle([FakeSource([candidate])])

    assert first.submitted == 1
    assert second.submitted == 0
    assert second.already_seen == 1
    assert len(submissions) == 1


async def test_duplicate_within_one_cycle_is_submitted_once(submissions, set_active_runs, budget):
    """The same post can top two feeds at once; the unique index must not blow up."""
    candidate = _candidate("t3_a")

    report = await run_cycle([FakeSource([candidate]), FakeSource([candidate])])

    assert report.fetched == 2
    assert report.submitted == 1
    assert len(submissions) == 1


async def test_filtered_candidate_is_recorded_but_not_submitted(
    submissions, set_active_runs, budget, session
):
    report = await run_cycle([FakeSource([_candidate("t3_short", chars=10)])])

    assert report.filtered == 1
    assert report.submitted == 0
    assert submissions == []

    rows = await _seen_rows(session)
    assert rows[0].status == SeenStatus.filtered
    assert rows[0].skip_reason == "too_short:10"


async def test_filtered_candidate_is_not_reconsidered_next_cycle(
    submissions, set_active_runs, budget
):
    """Recording rejects is what stops the scout re-fetching the same junk forever."""
    candidate = _candidate("t3_short", chars=10)

    await run_cycle([FakeSource([candidate])])
    second = await run_cycle([FakeSource([candidate])])

    assert second.already_seen == 1
    assert second.filtered == 0


async def test_backpressure_blocks_submission_when_queue_is_busy(
    submissions, set_active_runs, budget, session
):
    """Ingesting past the Buffer queue just turns fresh scripts into failed runs."""
    budget(max_pending_runs=5)
    set_active_runs(5)

    report = await run_cycle([FakeSource([_candidate("t3_a")])])

    assert report.skipped_no_capacity is True
    assert report.submitted == 0
    assert report.active_runs == 5
    assert submissions == []
    assert await _seen_rows(session) == []


async def test_no_capacity_still_records_filtered_items(
    submissions, set_active_runs, budget, session
):
    """A full queue costs nothing: the junk is still burned down for next time."""
    budget(max_pending_runs=5)
    set_active_runs(5)

    report = await run_cycle([FakeSource([_candidate("t3_short", chars=10)])])

    assert report.skipped_no_capacity is True
    assert report.filtered == 1
    rows = await _seen_rows(session)
    assert [r.status for r in rows] == [SeenStatus.filtered]


async def test_max_per_cycle_caps_submissions(submissions, set_active_runs, budget):
    budget(max_pending_runs=99, max_per_cycle=2)

    report = await run_cycle([FakeSource([_candidate(f"t3_{i}") for i in range(5)])])

    assert report.submitted == 2
    assert len(submissions) == 2


async def test_first_origin_does_not_monopolize_the_budget(
    submissions, set_active_runs, budget
):
    """Regression: the richest subreddit used to take every slot.

    Observed live before the fix — both submissions came from the first sub
    while the second contributed five candidates and won nothing.
    """
    budget(max_pending_runs=99, max_per_cycle=2)
    rich = [_candidate(f"a{i}", origin="r/desabafos") for i in range(13)]
    poor = [_candidate(f"b{i}", origin="r/relacionamentos") for i in range(5)]

    report = await run_cycle([FakeSource(rich + poor)])

    assert report.submitted == 2
    origins = {m["metadata"]["origin"] for m in submissions}
    assert origins == {"r/desabafos", "r/relacionamentos"}


async def test_rotation_reaches_every_origin_across_cycles(
    submissions, set_active_runs, budget
):
    """With 3 origins and a budget of 2, the third must not starve.

    Plain interleaving truncated at the budget yields ``a0, b0`` forever — the
    third origin only gets a turn once the first two run dry. Ordering the
    groups by how many each origin has already had published fixes it.
    """
    budget(max_pending_runs=99, max_per_cycle=2)

    def batch():
        return FakeSource(
            [_candidate(f"a{i}", origin="r/a") for i in range(3)]
            + [_candidate(f"b{i}", origin="r/b") for i in range(3)]
            + [_candidate(f"c{i}", origin="r/c") for i in range(3)]
        )

    for _ in range(3):
        await run_cycle([batch()])

    picked = [m["metadata"]["url"].rsplit("/", 1)[-1] for m in submissions]
    assert picked == ["a0", "b0", "c0", "a1", "b1", "c1"]


def test_interleave_puts_least_served_origin_first():
    ordered = interleave_by_origin(
        [_candidate("a1", origin="r/a"), _candidate("b1", origin="r/b")],
        usage={"r/a": 5, "r/b": 0},
    )
    assert [c.origin for c in ordered] == ["r/b", "r/a"]


def test_interleave_without_usage_is_deterministic_by_name():
    ordered = interleave_by_origin([
        _candidate("z1", origin="r/z"), _candidate("a1", origin="r/a"),
    ])
    assert [c.origin for c in ordered] == ["r/a", "r/z"]


async def test_remaining_capacity_caps_submissions_below_per_cycle(
    submissions, set_active_runs, budget
):
    """With 4 of 5 slots taken, only one more may go in even though the cap is 2."""
    budget(max_pending_runs=5, max_per_cycle=2)
    set_active_runs(4)

    report = await run_cycle([FakeSource([_candidate("t3_a"), _candidate("t3_b")])])

    assert report.submitted == 1


async def test_failed_submission_is_recorded_and_cycle_continues(
    monkeypatch, set_active_runs, budget, session
):
    budget(max_pending_runs=99, max_per_cycle=5)
    calls = []

    async def flaky_create(self, script, metadata):
        calls.append(metadata["url"])
        if metadata["url"].endswith("t3_bad"):
            raise RuntimeError("orchestrator down")
        return uuid.uuid4()

    monkeypatch.setattr(OrchestratorClient, "create_pipeline", flaky_create)

    report = await run_cycle([FakeSource([_candidate("t3_bad"), _candidate("t3_good")])])

    assert len(calls) == 2
    assert report.submitted == 1

    rows = {r.external_id: r for r in await _seen_rows(session)}
    assert rows["t3_bad"].status == SeenStatus.failed
    assert "orchestrator down" in rows["t3_bad"].skip_reason
    assert rows["t3_good"].status == SeenStatus.submitted


async def test_unsafe_candidate_is_recorded_and_skipped(
    submissions, set_active_runs, budget, moderation, session
):
    budget(max_pending_runs=99, max_per_cycle=2)
    moderation.set(
        lambda title, text: Verdict(safe=False, category="self_harm", reason="ideação descrita")
        if title == "ruim"
        else Verdict(safe=True)
    )

    report = await run_cycle([FakeSource([
        _candidate("t3_bad", title="ruim"),
        _candidate("t3_ok", title="boa"),
    ])])

    assert report.unsafe == 1
    assert report.submitted_ids == ["t3_ok"]

    rows = {r.external_id: r for r in await _seen_rows(session)}
    assert rows["t3_bad"].status == SeenStatus.filtered
    assert rows["t3_bad"].skip_reason == "unsafe:self_harm"


async def test_unsafe_candidate_does_not_consume_budget(
    submissions, set_active_runs, budget, moderation
):
    """A rejected story must not cost a slot — the next one takes it."""
    budget(max_pending_runs=99, max_per_cycle=2)
    moderation.set(
        lambda title, text: Verdict(safe=False, category="hate") if title == "ruim" else Verdict(safe=True)
    )

    report = await run_cycle([FakeSource([
        _candidate("t3_bad", title="ruim"),
        _candidate("t3_a", title="boa"),
        _candidate("t3_b", title="boa"),
    ])])

    assert report.submitted == 2
    assert report.submitted_ids == ["t3_a", "t3_b"]


async def test_moderation_only_runs_for_candidates_within_budget(
    submissions, set_active_runs, budget, moderation
):
    """Cost control: moderation is per-publication, not per-fetched-post."""
    budget(max_pending_runs=99, max_per_cycle=2)

    await run_cycle([FakeSource([_candidate(f"t3_{i}") for i in range(10)])])

    assert len(moderation.calls) == 2


async def test_length_filtered_candidates_are_never_moderated(
    submissions, set_active_runs, budget, moderation
):
    await run_cycle([FakeSource([_candidate("t3_short", chars=10)])])

    assert moderation.calls == []


async def test_moderation_outage_stops_cycle_without_consuming_candidates(
    submissions, set_active_runs, budget, moderation, session
):
    """"Could not check" must never read as either approval or rejection.

    Nothing is submitted, and nothing is recorded as seen — so the same stories
    are still available once the service is back.
    """
    budget(max_pending_runs=99, max_per_cycle=2)
    moderation.set(lambda title, text: ModerationError("connection refused"))

    report = await run_cycle([FakeSource([_candidate("t3_a"), _candidate("t3_b")])])

    assert report.moderation_unavailable is True
    assert report.submitted == 0
    assert report.unsafe == 0
    assert submissions == []
    assert await _seen_rows(session) == []


async def test_candidate_is_retried_after_moderation_recovers(
    submissions, set_active_runs, budget, moderation
):
    budget(max_pending_runs=99, max_per_cycle=2)
    moderation.set(lambda title, text: ModerationError("down"))
    await run_cycle([FakeSource([_candidate("t3_a")])])

    moderation.set(lambda title, text: Verdict(safe=True))
    report = await run_cycle([FakeSource([_candidate("t3_a")])])

    assert report.submitted == 1
    assert report.submitted_ids == ["t3_a"]


async def test_run_endpoint_returns_report(client, submissions, set_active_runs, budget, monkeypatch):
    monkeypatch.setattr(
        "src.content_scout.scout.build_sources", lambda: [FakeSource([_candidate("t3_a")])]
    )

    resp = await client.post("/scout/run")

    assert resp.status_code == 200
    body = resp.json()
    assert body["submitted"] == 1
    assert body["submitted_ids"] == ["t3_a"]


async def test_seen_endpoint_filters_by_status(client, submissions, set_active_runs, budget, monkeypatch):
    monkeypatch.setattr(
        "src.content_scout.scout.build_sources",
        lambda: [FakeSource([_candidate("t3_a"), _candidate("t3_short", chars=10)])],
    )
    await client.post("/scout/run")

    resp = await client.get("/scout/seen", params={"status": "filtered"})

    assert resp.status_code == 200
    body = resp.json()
    assert [i["external_id"] for i in body] == ["t3_short"]
    assert body[0]["skip_reason"] == "too_short:10"


# --------------------------------------------------------------------------
# Comment enrichment
# --------------------------------------------------------------------------


class FakeCommentSource(FakeSource):
    """A source that also answers for comments, like RedditSource does."""

    def __init__(self, candidates, thread=None, fail=False):
        super().__init__(candidates)
        self._thread = thread
        self._fail = fail
        self.asked: list[str] = []

    async def fetch_comments(self, candidate):
        self.asked.append(candidate.external_id)
        if self._fail:
            raise RuntimeError("boom")
        return self._thread


def _thread(total: int, bodies: int | None = None) -> CommentThread:
    bodies = total if bodies is None else bodies
    return CommentThread(
        total=total,
        comments=[
            Comment(
                external_id=f"t1_c{i}",
                author=f"/u/user{i}",
                text=f"reação {i}",
                position=i,
                published="2026-07-24T13:32:20+00:00",
            )
            for i in range(bodies)
        ],
    )


@pytest.fixture
def comments_cfg(monkeypatch):
    def _set(fetch_comments=True, max_comments_stored=20):
        monkeypatch.setattr(settings.CONFIG.scout, "fetch_comments", fetch_comments)
        monkeypatch.setattr(settings.CONFIG.scout, "max_comments_stored", max_comments_stored)
    _set()
    return _set


async def test_enrichment_stores_count_and_bodies(
    submissions, set_active_runs, budget, comments_cfg, session
):
    source = FakeCommentSource([_candidate("t3_a")], thread=_thread(3))

    report = await run_cycle([source])

    assert report.comments_fetched == 1
    assert source.asked == ["t3_a"]

    row = (await _seen_rows(session))[0]
    assert row.comment_count == 3
    assert [c.external_id for c in row.comments] == ["t1_c0", "t1_c1", "t1_c2"]
    assert row.comments[0].author == "/u/user0"
    assert row.comments[0].position == 0


async def test_comment_count_reaches_the_orchestrator(
    submissions, set_active_runs, budget, comments_cfg
):
    await run_cycle([FakeCommentSource([_candidate("t3_a")], thread=_thread(121))])
    assert submissions[0]["metadata"]["comment_count"] == 121


async def test_stored_bodies_are_capped_but_the_count_is_not(
    submissions, set_active_runs, budget, comments_cfg, session
):
    """The count is the signal; the bodies are only a sample of the thread."""
    comments_cfg(max_comments_stored=2)
    await run_cycle([FakeCommentSource([_candidate("t3_a")], thread=_thread(50))])

    row = (await _seen_rows(session))[0]
    assert row.comment_count == 50
    assert len(row.comments) == 2


async def test_enrichment_can_be_turned_off(
    submissions, set_active_runs, budget, comments_cfg, session
):
    """Each fetch costs a rate-limit window, so the cost has to be opt-out."""
    comments_cfg(fetch_comments=False)
    source = FakeCommentSource([_candidate("t3_a")], thread=_thread(3))

    report = await run_cycle([source])

    assert source.asked == []
    assert report.comments_fetched == 0
    assert (await _seen_rows(session))[0].comment_count is None


async def test_source_without_comment_support_is_skipped(
    submissions, set_active_runs, budget, comments_cfg, session
):
    """Comment support is optional — a plain Source must still submit fine."""
    report = await run_cycle([FakeSource([_candidate("t3_a")])])

    assert report.submitted == 1
    assert report.comments_fetched == 0
    assert (await _seen_rows(session))[0].comment_count is None


async def test_unavailable_comments_do_not_block_submission(
    submissions, set_active_runs, budget, comments_cfg, session
):
    """A 429 on the comment feed is not a reason to drop a story.

    ``None`` must land as NULL rather than 0 — the post was never counted, and
    recording a zero would claim it drew no reaction at all.
    """
    source = FakeCommentSource([_candidate("t3_a")], thread=None)

    report = await run_cycle([source])

    assert report.submitted == 1
    assert report.comments_fetched == 0
    row = (await _seen_rows(session))[0]
    assert row.comment_count is None
    assert row.comments == []


async def test_enrichment_failure_does_not_end_the_cycle(
    submissions, set_active_runs, budget, comments_cfg, session
):
    source = FakeCommentSource([_candidate("t3_a")], fail=True)

    report = await run_cycle([source])

    assert report.submitted == 1
    assert (await _seen_rows(session))[0].comment_count is None


async def test_filtered_candidates_are_never_enriched(
    submissions, set_active_runs, budget, comments_cfg, session
):
    """Enrichment costs a window per item — spending it on rejects is the whole
    thing the ordering exists to avoid."""
    source = FakeCommentSource(
        [_candidate("t3_short", chars=10), _candidate("t3_ok")], thread=_thread(2)
    )

    await run_cycle([source])

    assert source.asked == ["t3_ok"]
    rows = {r.external_id: r for r in await _seen_rows(session)}
    assert rows["t3_short"].comment_count is None
    assert rows["t3_ok"].comment_count == 2


async def test_author_is_recorded_from_the_candidate(
    submissions, set_active_runs, budget, comments_cfg, session
):
    candidate = Candidate(
        source="reddit",
        external_id="t3_a",
        origin="r/desabafos",
        title="T",
        text="a" * 1000,
        url="https://reddit.com/t3_a",
        extra={"author": "/u/fulano"},
    )
    await run_cycle([FakeSource([candidate])])

    assert (await _seen_rows(session))[0].author == "/u/fulano"


async def test_seen_endpoint_exposes_comments(
    client, submissions, set_active_runs, budget, comments_cfg, monkeypatch
):
    monkeypatch.setattr(
        "src.content_scout.scout.build_sources",
        lambda: [FakeCommentSource([_candidate("t3_a")], thread=_thread(7, bodies=2))],
    )
    await client.post("/scout/run")

    body = (await client.get("/scout/seen")).json()

    assert body[0]["comment_count"] == 7
    assert len(body[0]["comments"]) == 2
    assert body[0]["comments"][0]["text"] == "reação 0"


# ─────────────────────── story quality ───────────────────────


@pytest.fixture
def story_cfg(monkeypatch):
    def _set(story_quality=True, min_story_score=6):
        monkeypatch.setattr(settings.CONFIG.scout, "story_quality", story_quality)
        monkeypatch.setattr(settings.CONFIG.scout, "min_story_score", min_story_score)
    _set()
    return _set


async def test_strongest_opening_wins_the_slot(
    submissions, set_active_runs, budget, story_cfg, story_quality
):
    """The whole point: the slot goes to the best hook, not to feed position."""
    budget(max_per_cycle=1)
    story_quality.set({
        "t3_meh": StoryScore(hook=False, score=3),
        "t3_bom": StoryScore(hook=True, score=9, hook_line="ela se vingou de forma doce"),
    })
    source = FakeSource([_candidate("t3_meh"), _candidate("t3_bom")])

    report = await run_cycle([source])

    assert report.submitted_ids == ["t3_bom"]


async def test_weak_storytelling_is_tagged_but_still_published(
    submissions, set_active_runs, budget, story_cfg, story_quality, session
):
    """The score labels candidates, it does not gate them.

    A weak story with nothing better behind it still airs — dropping it would turn
    a soft quality signal into a hard filter and could empty the queue entirely.
    """
    story_quality.set({"t3_a": StoryScore(hook=False, score=1, reason="desabafo sem enredo")})

    report = await run_cycle([FakeSource([_candidate("t3_a")])])

    assert report.submitted == 1
    assert report.weak_storytelling == 1

    row = (await _seen_rows(session))[0]
    assert row.status == SeenStatus.submitted
    assert row.story_tag == TAG_WEAK
    assert row.story_score == 1
    assert row.has_hook is False
    assert row.story_reason == "desabafo sem enredo"


async def test_good_story_without_a_hook_is_tagged_no_hook(
    submissions, set_active_runs, budget, story_cfg, story_quality, session
):
    story_quality.set({"t3_a": StoryScore(hook=False, score=9)})

    report = await run_cycle([FakeSource([_candidate("t3_a")])])

    assert (await _seen_rows(session))[0].story_tag == TAG_NO_HOOK
    # A buried lede is not weak storytelling, and must not be counted as such.
    assert report.weak_storytelling == 0


async def test_hook_line_is_recorded_and_forwarded(
    submissions, set_active_runs, budget, story_cfg, story_quality, session
):
    story_quality.set({
        "t3_a": StoryScore(hook=True, score=9, hook_line="minha mãe se vingou de forma doce")
    })

    await run_cycle([FakeSource([_candidate("t3_a")])])

    row = (await _seen_rows(session))[0]
    assert row.hook_line == "minha mãe se vingou de forma doce"
    assert row.story_tag == TAG_STRONG

    meta = submissions[0]["metadata"]
    assert meta["story_tag"] == TAG_STRONG
    assert meta["story_score"] == 9
    assert meta["has_hook"] is True
    assert meta["hook_line"] == "minha mãe se vingou de forma doce"


async def test_metadata_omits_story_fields_when_unscored(
    submissions, set_active_runs, budget, story_cfg
):
    """The refiner must not be told a story is weak when nobody judged it."""
    await run_cycle([FakeSource([_candidate("t3_a")])])

    meta = submissions[0]["metadata"]
    assert "story_tag" not in meta
    assert "story_score" not in meta


async def test_outage_leaves_columns_null_and_still_publishes(
    submissions, set_active_runs, budget, story_cfg, session
):
    """Unlike moderation, losing this signal is not fatal — it gates nothing."""
    report = await run_cycle([FakeSource([_candidate("t3_a")])])

    assert report.submitted == 1
    assert report.story_quality_unavailable is True
    assert report.story_scored == 0

    row = (await _seen_rows(session))[0]
    assert row.story_tag is None
    assert row.story_score is None
    assert row.has_hook is None


async def test_filtered_candidates_are_never_scored(
    submissions, set_active_runs, budget, story_cfg, story_quality, session
):
    """Scoring runs after the cheap filters, so junk costs nothing."""
    report = await run_cycle([FakeSource([_candidate("t3_short", chars=10)])])

    assert report.filtered == 1
    assert story_quality.calls == [[]]

    row = (await _seen_rows(session))[0]
    assert row.story_tag is None
    assert row.skip_reason.startswith("too_short")


async def test_scoring_is_skipped_when_the_queue_is_full(
    submissions, set_active_runs, budget, story_cfg, story_quality
):
    """A full queue publishes nothing, so it must not pay for a judgement."""
    set_active_runs(5)

    report = await run_cycle([FakeSource([_candidate("t3_a")])])

    assert report.skipped_no_capacity is True
    assert story_quality.calls == []


async def test_one_batched_call_covers_every_candidate(
    submissions, set_active_runs, budget, story_cfg, story_quality
):
    """Batching is what makes a per-candidate signal affordable."""
    ids = [f"t3_{i}" for i in range(8)]

    await run_cycle([FakeSource([_candidate(i) for i in ids])])

    assert len(story_quality.calls) == 1
    assert story_quality.calls[0] == ids


async def test_disabled_story_quality_makes_no_call_and_writes_no_tag(
    submissions, set_active_runs, budget, story_cfg, story_quality, session
):
    story_cfg(story_quality=False)
    story_quality.set({"t3_a": StoryScore(hook=True, score=9)})

    report = await run_cycle([FakeSource([_candidate("t3_a")])])

    assert report.submitted == 1
    assert story_quality.calls == []
    assert report.story_scored == 0
    assert report.story_quality_unavailable is False
    assert (await _seen_rows(session))[0].story_tag is None


async def test_unsafe_candidate_still_records_its_story_tag(
    submissions, set_active_runs, budget, story_cfg, story_quality, moderation, session
):
    """The audit trail keeps both verdicts — they were both paid for."""
    moderation.set(lambda title, text: Verdict(safe=False, category="self_harm"))
    story_quality.set({"t3_a": StoryScore(hook=True, score=8)})

    await run_cycle([FakeSource([_candidate("t3_a")])])

    row = (await _seen_rows(session))[0]
    assert row.skip_reason == "unsafe:self_harm"
    assert row.story_tag == TAG_STRONG
    assert row.story_score == 8


async def test_report_counts_scored_and_weak(
    submissions, set_active_runs, budget, story_cfg, story_quality
):
    budget(max_per_cycle=1)
    story_quality.set({
        "t3_a": StoryScore(hook=True, score=9),
        "t3_b": StoryScore(hook=False, score=2),
        "t3_c": StoryScore(hook=False, score=5),
    })

    report = await run_cycle([FakeSource([_candidate(i) for i in ("t3_a", "t3_b", "t3_c")])])

    assert report.story_scored == 3
    # Counted over everything judged this cycle, not just what was published.
    assert report.weak_storytelling == 2


async def test_threshold_is_configurable(
    submissions, set_active_runs, budget, story_cfg, story_quality, session
):
    """Moving the line is how the tag gets calibrated against real data."""
    story_cfg(min_story_score=3)
    story_quality.set({"t3_a": StoryScore(hook=True, score=5)})

    await run_cycle([FakeSource([_candidate("t3_a")])])

    assert (await _seen_rows(session))[0].story_tag == TAG_STRONG


async def test_seen_endpoint_filters_by_story_tag(
    client, submissions, set_active_runs, budget, story_cfg, story_quality, monkeypatch
):
    story_quality.set({
        "t3_bom": StoryScore(hook=True, score=9),
        "t3_ruim": StoryScore(hook=False, score=1),
    })
    monkeypatch.setattr(
        "src.content_scout.scout.build_sources",
        lambda: [FakeSource([_candidate("t3_bom"), _candidate("t3_ruim")])],
    )
    await client.post("/scout/run")

    weak = (await client.get(f"/scout/seen?story_tag={TAG_WEAK}")).json()

    assert [row["external_id"] for row in weak] == ["t3_ruim"]
    assert weak[0]["story_score"] == 1
    assert weak[0]["has_hook"] is False
