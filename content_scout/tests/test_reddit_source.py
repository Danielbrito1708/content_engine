import pytest

from src.content_scout.sources.reddit import RedditSource, clean_body, parse_feed

pytestmark = pytest.mark.no_db


def _entry(entry_id: str, title: str, content: str) -> str:
    return f"""
  <entry>
    <author><name>/u/someone</name></author>
    <category term="desabafos" label="r/desabafos"/>
    <content type="html">{content}</content>
    <id>{entry_id}</id>
    <link href="https://www.reddit.com/r/desabafos/comments/abc/x/"/>
    <updated>2026-07-22T18:49:20+00:00</updated>
    <published>2026-07-22T18:49:20+00:00</published>
    <title>{title}</title>
  </entry>"""


def _feed(*entries: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f'{"".join(entries)}'
        "</feed>"
    )


# Reddit double-escapes the body, so a fixture has to as well to be realistic.
REAL_CONTENT = (
    "&lt;!-- SC_OFF --&gt;&lt;div class=\"md\"&gt;&lt;p&gt;Primeiro par&amp;#225;grafo da "
    "hist&amp;#243;ria.&lt;/p&gt; &lt;p&gt;Segundo par&amp;#225;grafo.&lt;/p&gt; &lt;/div&gt;"
    "&lt;!-- SC_ON --&gt;   submitted by   &lt;a href=\"https://www.reddit.com/user/x\"&gt; "
    "/u/x &lt;/a&gt; &lt;br/&gt; &lt;span&gt;&lt;a href=\"https://reddit.com/y\"&gt;[link]"
    "&lt;/a&gt;&lt;/span&gt;   &lt;span&gt;&lt;a href=\"https://reddit.com/y\"&gt;[comments]"
    "&lt;/a&gt;&lt;/span&gt;"
)


def test_clean_body_drops_submitted_by_footer():
    text = clean_body(REAL_CONTENT)
    assert "submitted by" not in text
    assert "[link]" not in text
    assert "[comments]" not in text
    assert "/u/x" not in text


def test_clean_body_decodes_entities_and_keeps_paragraphs():
    text = clean_body(REAL_CONTENT)
    assert "Primeiro parágrafo da história." in text
    assert "Segundo parágrafo." in text
    assert "\n\n" in text


def test_clean_body_strips_zero_width_and_nbsp():
    raw = "&lt;!-- SC_OFF --&gt;&lt;div&gt;&lt;p&gt;​Oi mundo&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt;"
    assert clean_body(raw) == "Oi mundo"


def test_clean_body_without_markers_falls_back_to_whole_content():
    assert clean_body("&lt;p&gt;Sem marcadores&lt;/p&gt;") == "Sem marcadores"


def test_clean_body_empty_returns_empty():
    assert clean_body("") == ""


def test_parse_feed_extracts_candidate_fields():
    feed = _feed(_entry("t3_abc123", "Um título", REAL_CONTENT))
    candidates = parse_feed(feed, "desabafos")

    assert len(candidates) == 1
    c = candidates[0]
    assert c.source == "reddit"
    assert c.external_id == "t3_abc123"
    assert c.origin == "r/desabafos"
    assert c.title == "Um título"
    assert c.url == "https://www.reddit.com/r/desabafos/comments/abc/x/"
    assert c.extra["published"] == "2026-07-22T18:49:20+00:00"
    assert c.char_count == len(c.text)


def test_parse_feed_drops_entries_without_body():
    """Link and image posts have no selftext — there is no script to extract."""
    empty = "&lt;!-- SC_OFF --&gt;&lt;div&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt; submitted by"
    feed = _feed(
        _entry("t3_ok", "Com corpo", REAL_CONTENT),
        _entry("t3_link", "Só link", empty),
    )
    candidates = parse_feed(feed, "desabafos")
    assert [c.external_id for c in candidates] == ["t3_ok"]


def test_parse_feed_empty_feed():
    assert parse_feed(_feed(), "desabafos") == []


def test_candidate_metadata_carries_provenance():
    feed = _feed(_entry("t3_abc123", "Um título", REAL_CONTENT))
    meta = parse_feed(feed, "desabafos")[0].to_metadata()
    assert meta["source"] == "reddit"
    assert meta["origin"] == "r/desabafos"
    assert meta["title"] == "Um título"
    assert meta["url"].startswith("https://www.reddit.com/")


def test_feed_url_requests_top_over_window():
    source = RedditSource(
        base_url="https://www.reddit.com/",
        subreddits=["desabafos"],
        time_filter="week",
        limit_per_subreddit=15,
        user_agent="test",
        request_delay=0,
    )
    url = source.feed_url("desabafos")
    assert url == "https://www.reddit.com/r/desabafos/top/.rss?t=week&limit=15"


async def test_fetch_survives_one_failing_subreddit(monkeypatch):
    """A private or renamed sub is routine — it must not abort the whole cycle."""
    import httpx

    source = RedditSource(
        base_url="https://www.reddit.com",
        subreddits=["boa", "quebrada"],
        time_filter="week",
        limit_per_subreddit=5,
        user_agent="test",
        request_delay=0,
    )

    async def fake_get(self, url, **kwargs):
        if "quebrada" in url:
            raise httpx.ConnectError("boom")
        return httpx.Response(
            200,
            text=_feed(_entry("t3_ok", "T", REAL_CONTENT)),
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    candidates = await source.fetch()
    assert [c.external_id for c in candidates] == ["t3_ok"]


async def test_fetch_survives_malformed_xml(monkeypatch):
    import httpx

    source = RedditSource(
        base_url="https://www.reddit.com",
        subreddits=["desabafos"],
        time_filter="week",
        limit_per_subreddit=5,
        user_agent="test",
        request_delay=0,
    )

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, text="<not-xml", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await source.fetch() == []


async def test_fetch_survives_rate_limit(monkeypatch):
    """429 is the expected answer when requests are too close together.

    Observed live: unauthenticated feeds answer ``x-ratelimit-remaining: 0`` with
    a ~40s reset, so a throttled sub must be skipped, not crash the cycle.
    """
    import httpx

    source = RedditSource(
        base_url="https://www.reddit.com",
        subreddits=["limitada", "boa"],
        time_filter="week",
        limit_per_subreddit=5,
        user_agent="test",
        request_delay=0,
    )

    async def fake_get(self, url, **kwargs):
        if "limitada" in url:
            return httpx.Response(
                429,
                headers={"x-ratelimit-reset": "40", "x-ratelimit-remaining": "0.0"},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            text=_feed(_entry("t3_ok", "T", REAL_CONTENT)),
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    candidates = await source.fetch()
    assert [c.external_id for c in candidates] == ["t3_ok"]


async def test_fetch_spaces_requests_apart(monkeypatch):
    """Without spacing only the first subreddit ever returns 200."""
    import httpx

    source = RedditSource(
        base_url="https://www.reddit.com",
        subreddits=["a", "b", "c"],
        time_filter="week",
        limit_per_subreddit=5,
        user_agent="test",
        request_delay=45,
    )

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    async def fake_get(self, url, **kwargs):
        return httpx.Response(
            200,
            text=_feed(_entry(f"t3_{url[-20:]}", "T", REAL_CONTENT)),
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("src.content_scout.sources.reddit.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    await source.fetch()

    # Three subreddits, two gaps — and never before the first request.
    assert slept == [45, 45]
