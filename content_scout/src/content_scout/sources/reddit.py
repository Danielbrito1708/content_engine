import asyncio
import html
import re
import time
import xml.etree.ElementTree as ET

import httpx
import structlog

from src.content_scout.sources.base import Candidate, Comment, CommentThread

log = structlog.get_logger(__name__)

ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# Reddit's fullname prefixes: t3 is a submission, t1 a comment. The comment feed
# returns the submission as its first entry, so the prefix is what separates the
# post from the reactions to it.
POST_PREFIX = "t3_"
COMMENT_PREFIX = "t1_"

# Reddit wraps the self-post body in these markers and appends a
# "submitted by /u/x [link] [comments]" footer after SC_ON. Narrating that footer
# is exactly the kind of thing that ends up spoken out loud in the final video.
_BODY_RE = re.compile(r"<!--\s*SC_OFF\s*-->(.*?)<!--\s*SC_ON\s*-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
# nbsp, zero-width space and bidi marks: Reddit bodies are full of these and
# they survive tag stripping to reach the TTS as unpronounceable garbage.
_WS_RE = re.compile("[ \t\u00a0\u200b\u200e\u200f]+")
_BLANKS_RE = re.compile(r"\n{3,}")


class _Throttle:
    """Spaces out every Reddit request, whatever endpoint it targets.

    The limit is per client, not per endpoint: measured live, a comment feed
    fetched right after a subreddit feed answers 429 with ``x-ratelimit-used: 1``
    and ``reset: 58`` — the listing request had already spent the window. Keeping
    the delay inside ``fetch`` would therefore only protect the calls that method
    happens to make, and every new endpoint would have to re-invent the spacing.
    """

    def __init__(self, delay: float):
        self._delay = delay
        self._lock = asyncio.Lock()
        self._last: float | None = None

    async def wait(self) -> None:
        async with self._lock:
            if self._last is not None:
                remaining = self._last + self._delay - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last = time.monotonic()


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
                extra={
                    "published": _text(entry, "a:published"),
                    "author": _author(entry),
                },
            )
        )

    return candidates


def parse_comments(xml_text: str) -> CommentThread:
    """Parse a post's comment feed into a thread.

    The feed leads with the submission itself (``t3_``) and follows with the
    replies (``t1_``), flattened — Atom carries no nesting, so depth is lost and
    only the source's own ordering survives, as ``position``.

    Replies whose body did not survive cleaning (deleted, removed, image-only)
    are dropped from ``comments`` but still counted in ``total``: they existed on
    the post, and the count is meant to measure how much reaction it drew.
    """
    root = ET.fromstring(xml_text)
    comments: list[Comment] = []
    total = 0

    for entry in root.findall("a:entry", ATOM_NS):
        external_id = _text(entry, "a:id")
        if not external_id.startswith(COMMENT_PREFIX):
            continue

        total += 1
        body = clean_body(_text(entry, "a:content"))
        if not body:
            continue

        comments.append(
            Comment(
                external_id=external_id,
                author=_author(entry),
                text=body,
                position=total - 1,
                published=_text(entry, "a:updated"),
            )
        )

    return CommentThread(total=total, comments=comments)


def _text(entry: ET.Element, path: str) -> str:
    el = entry.find(path, ATOM_NS)
    return (el.text or "") if el is not None else ""


def _author(entry: ET.Element) -> str:
    """``/u/name`` as Reddit writes it, empty when the account is gone."""
    return _text(entry, "a:author/a:name").strip()


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
                 request_delay: float = 60.0, comments_limit: int = 0):
        self._base_url = base_url.rstrip("/")
        self._subreddits = subreddits
        self._time_filter = time_filter
        self._limit = limit_per_subreddit
        self._user_agent = user_agent
        self._timeout = timeout
        self._comments_limit = comments_limit
        self._throttle = _Throttle(request_delay)

    def feed_url(self, subreddit: str) -> str:
        return (
            f"{self._base_url}/r/{subreddit}/top/.rss"
            f"?t={self._time_filter}&limit={self._limit}"
        )

    def comments_url(self, external_id: str) -> str:
        """Comment feed for a submission, built from its fullname.

        Uses the id36 short form rather than the candidate's permalink: permalinks
        embed a slug made from the title, which for these subreddits is full of
        accented characters and would need escaping to survive as a URL.
        """
        id36 = external_id.removeprefix(POST_PREFIX)
        url = f"{self._base_url}/comments/{id36}/.rss"
        return f"{url}?limit={self._comments_limit}" if self._comments_limit else url

    async def _get(self, url: str, **log_context) -> str | None:
        """One throttled request. Returns ``None`` instead of raising.

        Every caller wants the same thing from a failure — log it, skip that
        item, keep the cycle alive — so the handling lives here rather than being
        repeated at each call site.
        """
        await self._throttle.wait()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except httpx.HTTPStatusError as exc:
            log.warning(
                "reddit_rate_limited" if exc.response.status_code == 429 else "reddit_request_failed",
                status=exc.response.status_code,
                reset=exc.response.headers.get("x-ratelimit-reset"),
                **log_context,
            )
        except httpx.HTTPError as exc:
            log.warning("reddit_request_failed", error=str(exc), **log_context)
        return None

    async def fetch(self) -> list[Candidate]:
        """Fetch every configured subreddit, one request at a time.

        Unauthenticated reads are rate-limited to roughly one request per 40-60s:
        the response carries ``x-ratelimit-remaining: 0`` and a
        ``x-ratelimit-reset`` of ~40 even on the very first call. Firing the
        subreddits back to back means only the first one ever returns 200 and
        every other feed silently 429s — hence the shared throttle.

        One failing subreddit must not take the whole cycle down — private,
        renamed and rate-limited subs are normal occurrences, not outages.
        """
        candidates: list[Candidate] = []

        for subreddit in self._subreddits:
            xml_text = await self._get(self.feed_url(subreddit), subreddit=subreddit)
            if xml_text is None:
                continue
            try:
                found = parse_feed(xml_text, subreddit)
            except ET.ParseError as exc:
                log.warning("reddit_feed_unparseable", subreddit=subreddit, error=str(exc))
                continue

            log.info("reddit_feed_fetched", subreddit=subreddit, candidates=len(found))
            candidates.extend(found)

        return candidates

    async def fetch_comments(self, candidate: Candidate) -> CommentThread | None:
        """Reactions to one candidate, or ``None`` when they could not be read.

        Costs a full rate-limit window per candidate, which is why the scout only
        calls this for posts it is about to publish — see ``run_cycle``. ``None``
        is distinct from an empty thread: a post genuinely without replies is a
        real signal, a 429 is not.
        """
        xml_text = await self._get(
            self.comments_url(candidate.external_id), external_id=candidate.external_id
        )
        if xml_text is None:
            return None
        try:
            thread = parse_comments(xml_text)
        except ET.ParseError as exc:
            log.warning(
                "reddit_comments_unparseable", external_id=candidate.external_id, error=str(exc)
            )
            return None

        log.info(
            "reddit_comments_fetched",
            external_id=candidate.external_id,
            total=thread.total,
            stored=len(thread.comments),
        )
        return thread
