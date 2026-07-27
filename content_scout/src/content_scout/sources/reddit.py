import asyncio
import html
import re
import xml.etree.ElementTree as ET

import httpx
import structlog

from src.content_scout.sources.base import Candidate

log = structlog.get_logger(__name__)

ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# Reddit wraps the self-post body in these markers and appends a
# "submitted by /u/x [link] [comments]" footer after SC_ON. Narrating that footer
# is exactly the kind of thing that ends up spoken out loud in the final video.
_BODY_RE = re.compile(r"<!--\s*SC_OFF\s*-->(.*?)<!--\s*SC_ON\s*-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
# nbsp, zero-width space and bidi marks: Reddit bodies are full of these and
# they survive tag stripping to reach the TTS as unpronounceable garbage.
_WS_RE = re.compile("[ \t\u00a0\u200b\u200e\u200f]+")
_BLANKS_RE = re.compile(r"\n{3,}")


def clean_body(raw_content: str) -> str:
    """Turn the Atom ``<content>`` payload into plain narratable text.

    The payload is HTML-escaped HTML, so it needs unescaping before tags can be
    stripped, and unescaping again for entities that live inside the text nodes.
    Paragraph breaks are preserved because they are the narration's pacing.
    """
    if not raw_content:
        return ""

    unescaped = html.unescape(raw_content)
    match = _BODY_RE.search(unescaped)
    body = match.group(1) if match else unescaped

    body = re.sub(r"</p\s*>", "\n\n", body, flags=re.IGNORECASE)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.IGNORECASE)
    text = html.unescape(_TAG_RE.sub("", body))

    lines = [_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANKS_RE.sub("\n\n", "\n".join(lines)).strip()


def parse_feed(xml_text: str, subreddit: str) -> list[Candidate]:
    """Parse a subreddit Atom feed into candidates.

    Entries without a usable body (link posts, image posts) are dropped here —
    they carry no script, so there is nothing downstream can do with them.
    """
    root = ET.fromstring(xml_text)
    candidates: list[Candidate] = []

    for entry in root.findall("a:entry", ATOM_NS):
        external_id = _text(entry, "a:id")
        title = _text(entry, "a:title")
        content = _text(entry, "a:content")
        if not external_id or not title:
            continue

        body = clean_body(content)
        if not body:
            continue

        link_el = entry.find("a:link", ATOM_NS)
        url = link_el.get("href", "") if link_el is not None else ""

        candidates.append(
            Candidate(
                source="reddit",
                external_id=external_id,
                origin=f"r/{subreddit}",
                title=title.strip(),
                text=body,
                url=url,
                extra={"published": _text(entry, "a:published")},
            )
        )

    return candidates


def _text(entry: ET.Element, path: str) -> str:
    el = entry.find(path, ATOM_NS)
    return (el.text or "") if el is not None else ""


class RedditSource:
    """Reads subreddit ``top`` Atom feeds.

    Reddit killed unauthenticated ``.json`` in May 2026; the RSS/Atom feeds stayed
    open, need no credentials, and are not covered by the Data API's
    non-commercial clause. The tradeoff is that feeds carry no score — so we ask
    for ``/top/`` over a time window and let Reddit's own ranking be the quality
    signal, implicitly, by position.
    """

    name = "reddit"

    def __init__(self, base_url: str, subreddits: list[str], time_filter: str,
                 limit_per_subreddit: int, user_agent: str, timeout: int = 20,
                 request_delay: float = 60.0):
        self._base_url = base_url.rstrip("/")
        self._subreddits = subreddits
        self._time_filter = time_filter
        self._limit = limit_per_subreddit
        self._user_agent = user_agent
        self._timeout = timeout
        self._request_delay = request_delay

    def feed_url(self, subreddit: str) -> str:
        return (
            f"{self._base_url}/r/{subreddit}/top/.rss"
            f"?t={self._time_filter}&limit={self._limit}"
        )

    async def fetch(self) -> list[Candidate]:
        """Fetch every configured subreddit, one request at a time.

        Unauthenticated feed reads are rate-limited to roughly one request per
        40s: the response carries ``x-ratelimit-remaining: 0`` and a
        ``x-ratelimit-reset`` of ~40 even on the very first call. Firing the
        subreddits back to back means only the first one ever returns 200 and
        every other feed silently 429s — so requests are spaced by
        ``request_delay``.

        One failing subreddit must not take the whole cycle down — private,
        renamed and rate-limited subs are normal occurrences, not outages.
        """
        candidates: list[Candidate] = []

        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers={"User-Agent": self._user_agent},
            follow_redirects=True,
        ) as client:
            for index, subreddit in enumerate(self._subreddits):
                if index and self._request_delay:
                    await asyncio.sleep(self._request_delay)

                url = self.feed_url(subreddit)
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    found = parse_feed(resp.text, subreddit)
                except httpx.HTTPStatusError as exc:
                    log.warning(
                        "reddit_feed_rate_limited"
                        if exc.response.status_code == 429
                        else "reddit_feed_failed",
                        subreddit=subreddit,
                        status=exc.response.status_code,
                        reset=exc.response.headers.get("x-ratelimit-reset"),
                    )
                    continue
                except (httpx.HTTPError, ET.ParseError) as exc:
                    log.warning("reddit_feed_failed", subreddit=subreddit, error=str(exc))
                    continue

                log.info("reddit_feed_fetched", subreddit=subreddit, candidates=len(found))
                candidates.extend(found)

        return candidates
