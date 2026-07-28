"""One call policy for every service-to-service request the pipeline makes.

A pipeline run is expensive: by the time the render starts it has already paid
for an LLM call, a speech synthesis and a Whisper transcription. Letting a single
dropped connection or a 503 from a container that is still booting throw all of
that away is the difference between a pipeline that survives a night and one that
needs a human every morning.

What is retried is deliberately narrow — transport failures and the 5xx family,
which say "ask again". A 4xx is never retried: it is a deterministic answer, and
that includes the poster's ``429 buffer_queue_full``, which is a scheduling
decision the worker handles rather than an error to hammer at.
"""

import asyncio

import httpx
from structlog import get_logger

log = get_logger(__name__)

ATTEMPTS = 3
#: Base of the exponential backoff, in seconds: 2s, then 4s.
BACKOFF = 2.0
RETRIABLE_STATUS = frozenset({500, 502, 503, 504})


async def request(
    method: str,
    url: str,
    *,
    timeout: float,
    attempts: int = ATTEMPTS,
    **kwargs,
) -> httpx.Response:
    """Perform a request, retrying only what is worth retrying.

    Raises the last transport error, or ``httpx.HTTPStatusError`` for any status
    that survived the retries — callers keep the exception types they already
    handle.
    """
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_error = exc
            if attempt >= attempts:
                break
            log.warning(
                "http_retry", url=url, attempt=attempt, error=str(exc), reason="transport"
            )
            await asyncio.sleep(BACKOFF**attempt)
            continue

        if resp.status_code in RETRIABLE_STATUS and attempt < attempts:
            log.warning(
                "http_retry", url=url, attempt=attempt, status=resp.status_code, reason="status"
            )
            await asyncio.sleep(BACKOFF**attempt)
            continue

        resp.raise_for_status()
        return resp

    assert last_error is not None  # only reachable through the transport branch
    raise last_error
