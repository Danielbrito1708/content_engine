import json

import pytest

from src.core import settings
from src.llm_service.llm.base import BaseLLMClient
from src.llm_service.prompts.story import SYSTEM_PROMPT, build_user_prompt
from src.llm_service.schemas.story import StoryItem, StoryVerdict


class FakeClient(BaseLLMClient):
    def __init__(self, raw: str | Exception):
        self._raw = raw
        self.seen: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.seen.append((system, user))
        if isinstance(self._raw, Exception):
            raise self._raw
        return self._raw


@pytest.fixture
def fake_llm(monkeypatch):
    def _install(raw: str | Exception) -> FakeClient:
        client = FakeClient(raw)
        monkeypatch.setattr(
            "src.llm_service.api.routes.story.get_llm_client", lambda model=None: client
        )
        return client

    return _install


def _items(*openings) -> list[dict]:
    return [{"index": i, "opening": text} for i, text in enumerate(openings)]


async def test_scores_a_batch(client, fake_llm):
    fake_llm(
        json.dumps(
            {
                "results": [
                    {
                        "index": 0,
                        "hook": True,
                        "score": 9,
                        "hook_line": "minha mãe se vingou de forma doce",
                        "reason": "conflito claro e desfecho prometido",
                    },
                    {"index": 1, "hook": False, "score": 2, "reason": "desabafo sem enredo"},
                ]
            }
        )
    )

    resp = await client.post("/story-quality", json={"items": _items("a", "b")})

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["index"] for r in results] == [0, 1]
    assert results[0]["hook"] is True
    assert results[0]["score"] == 9
    assert results[0]["hook_line"] == "minha mãe se vingou de forma doce"
    assert results[1]["hook"] is False
    assert results[1]["hook_line"] is None


async def test_empty_batch_skips_the_llm(client, fake_llm):
    """Nothing to judge means nothing to pay for."""
    fake = fake_llm(json.dumps({"results": []}))

    resp = await client.post("/story-quality", json={"items": []})

    assert resp.status_code == 200
    assert resp.json() == {"results": []}
    assert fake.seen == []


async def test_out_of_order_verdicts_stay_matched_to_their_index(client, fake_llm):
    """The index is the contract — the model's ordering is not trusted."""
    fake_llm(
        json.dumps(
            {
                "results": [
                    {"index": 1, "hook": False, "score": 3},
                    {"index": 0, "hook": True, "score": 8},
                ]
            }
        )
    )

    resp = await client.post("/story-quality", json={"items": _items("a", "b")})

    by_index = {r["index"]: r for r in resp.json()["results"]}
    assert by_index[0]["score"] == 8
    assert by_index[1]["score"] == 3


async def test_one_malformed_verdict_does_not_lose_the_others(client, fake_llm):
    """A batch is only cheaper than N calls if it does not fail as one."""
    fake_llm(
        json.dumps(
            {
                "results": [
                    {"index": 0, "hook": True},  # no score
                    {"index": 1, "hook": True, "score": 7},
                ]
            }
        )
    )

    resp = await client.post("/story-quality", json={"items": _items("a", "b")})

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["index"] for r in results] == [1]


async def test_unrequested_index_is_dropped(client, fake_llm):
    fake_llm(
        json.dumps(
            {
                "results": [
                    {"index": 0, "hook": True, "score": 7},
                    {"index": 42, "hook": True, "score": 10},
                ]
            }
        )
    )

    resp = await client.post("/story-quality", json={"items": _items("a")})

    assert [r["index"] for r in resp.json()["results"]] == [0]


async def test_duplicated_index_keeps_only_the_first(client, fake_llm):
    fake_llm(
        json.dumps(
            {
                "results": [
                    {"index": 0, "hook": True, "score": 8},
                    {"index": 0, "hook": False, "score": 1},
                ]
            }
        )
    )

    results = (await client.post("/story-quality", json={"items": _items("a")})).json()["results"]
    assert len(results) == 1
    assert results[0]["score"] == 8


async def test_out_of_range_score_is_clamped(client, fake_llm):
    fake_llm(
        json.dumps(
            {"results": [{"index": 0, "hook": True, "score": 47}, {"index": 1, "hook": False, "score": -5}]}
        )
    )

    results = (
        await client.post("/story-quality", json={"items": _items("a", "b")})
    ).json()["results"]
    assert results[0]["score"] == 10
    assert results[1]["score"] == 0


async def test_fenced_json_is_tolerated(client, fake_llm):
    fake_llm('```json\n{"results": [{"index": 0, "hook": true, "score": 6}]}\n```')

    resp = await client.post("/story-quality", json={"items": _items("a")})

    assert resp.status_code == 200
    assert resp.json()["results"][0]["score"] == 6


async def test_invalid_json_is_502(client, fake_llm):
    fake_llm("não consigo avaliar isso")

    resp = await client.post("/story-quality", json={"items": _items("a")})

    assert resp.status_code == 502


async def test_missing_results_key_is_502(client, fake_llm):
    fake_llm(json.dumps({"verdicts": []}))

    resp = await client.post("/story-quality", json={"items": _items("a")})

    assert resp.status_code == 502


async def test_no_usable_verdict_is_502(client, fake_llm):
    """An answer that scores nothing is a failure, not an empty result."""
    fake_llm(json.dumps({"results": [{"index": 9, "hook": True, "score": 5}]}))

    resp = await client.post("/story-quality", json={"items": _items("a")})

    assert resp.status_code == 502


async def test_llm_failure_is_502(client, fake_llm):
    fake_llm(RuntimeError("upstream down"))

    resp = await client.post("/story-quality", json={"items": _items("a")})

    assert resp.status_code == 502
    assert "upstream down" in resp.json()["detail"]


async def test_titles_and_openings_reach_the_prompt(client, fake_llm):
    fake = fake_llm(json.dumps({"results": [{"index": 0, "hook": True, "score": 7}]}))

    await client.post(
        "/story-quality",
        json={"items": [{"index": 0, "title": "Minha sogra", "opening": "Ela chegou sem avisar"}]},
    )

    _system, user = fake.seen[0]
    assert "Minha sogra" in user
    assert "Ela chegou sem avisar" in user


async def test_route_uses_the_story_model(client, monkeypatch):
    """Judging craft over a batch must not silently burn the refinement model."""
    seen = {}

    def fake_factory(model=None):
        seen["model"] = model
        return FakeClient(json.dumps({"results": [{"index": 0, "hook": True, "score": 5}]}))

    monkeypatch.setattr("src.llm_service.api.routes.story.get_llm_client", fake_factory)

    await client.post("/story-quality", json={"items": _items("a")})

    assert seen["model"] == settings.env.llm_story_model


def test_story_model_falls_back_to_llm_model():
    """No extra config required — it works out of the box."""
    assert settings.env.llm_story_model == settings.env.llm_model


def test_user_prompt_numbers_every_block():
    items = [
        StoryItem(index=0, title="t0", opening="o0"),
        StoryItem(index=1, opening="o1"),
    ]

    prompt = build_user_prompt(items)

    assert "### 0" in prompt and "### 1" in prompt
    assert "TÍTULO: t0" in prompt
    # An untitled candidate gets no empty header line.
    assert "TÍTULO: \n" not in prompt
    assert "2 histórias" in prompt


def test_non_numeric_score_still_fails_validation():
    """Clamping handles range, not type — garbage must not become a score."""
    with pytest.raises(Exception):
        StoryVerdict.model_validate({"index": 0, "hook": True, "score": "muito bom"})


def test_prompt_asks_for_the_whole_scale():
    """Nenhum post tirou 9-10 na medição: a régua virava efetivamente 2-8."""
    assert "Use a escala inteira" in SYSTEM_PROMPT


def test_prompt_no_longer_reserves_the_top_for_the_exceptional():
    assert "Reserve 9–10" not in SYSTEM_PROMPT


def test_prompt_still_puts_an_ordinary_post_in_the_middle():
    """Soltar o topo não é inflacionar a régua inteira — o meio não se move."""
    assert "post comum de fórum é 4–6" in SYSTEM_PROMPT


# ─────────────────────────── revolta e vilão ───────────────────────────


async def test_outrage_and_villain_come_back_in_the_verdict(client, fake_llm):
    fake_llm(
        json.dumps(
            {
                "results": [
                    {
                        "index": 0,
                        "hook": True,
                        "score": 7,
                        "outrage": 9,
                        "villain": True,
                        "reason": "namorado traiu e quer voltar",
                    }
                ]
            }
        )
    )

    resp = await client.post("/story-quality", json={"items": _items("a")})

    assert resp.status_code == 200
    verdict = resp.json()["results"][0]
    assert verdict["outrage"] == 9
    assert verdict["villain"] is True


async def test_missing_outrage_is_none_not_zero(client, fake_llm):
    """Unanswered is not a judgement — the caller sorts it at the neutral point."""
    fake_llm(json.dumps({"results": [{"index": 0, "hook": True, "score": 7}]}))

    verdict = (await client.post("/story-quality", json={"items": _items("a")})).json()["results"][0]

    assert verdict["outrage"] is None
    assert verdict["villain"] is False


async def test_outrage_out_of_range_is_clamped_not_rejected(client, fake_llm):
    """Same rule as ``score``: one bad number must not cost the whole batch."""
    fake_llm(
        json.dumps(
            {
                "results": [
                    {"index": 0, "hook": True, "score": 7, "outrage": 47},
                    {"index": 1, "hook": True, "score": 7, "outrage": -3},
                ]
            }
        )
    )

    results = (
        await client.post("/story-quality", json={"items": _items("a", "b")})
    ).json()["results"]

    assert results[0]["outrage"] == 10
    assert results[1]["outrage"] == 0


def test_verdict_defaults_keep_outrage_unjudged():
    verdict = StoryVerdict(index=0, hook=True, score=5)
    assert verdict.outrage is None
    assert verdict.villain is False


def test_prompt_defines_outrage_and_villain():
    assert '"outrage"' in SYSTEM_PROMPT
    assert '"villain"' in SYSTEM_PROMPT
    assert "0–2" in SYSTEM_PROMPT and "8–10" in SYSTEM_PROMPT


def test_prompt_names_the_core_audience():
    """The top of the outrage scale is reserved for the audience that converts."""
    assert "mulher" in SYSTEM_PROMPT
    assert "18 a 35" in SYSTEM_PROMPT


def test_prompt_keeps_outrage_separate_from_storytelling():
    """Two axes that may disagree — collapsing them would hide which is missing."""
    assert "notas separadas e podem discordar" in SYSTEM_PROMPT


def test_prompt_does_not_turn_outrage_into_a_safety_call():
    """Judging reaction is not approving the behaviour — moderation decides that."""
    assert "não é de moral e não é de segurança" in SYSTEM_PROMPT or (
        "não de moral e não de segurança" in SYSTEM_PROMPT
    )
