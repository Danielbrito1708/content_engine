import httpx
import pytest
import respx
from httpx import Response

from src.orchestrator.clients import http


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """The policy is under test, not the wall clock — 0**n is 0."""
    monkeypatch.setattr(http, "BACKOFF", 0)


@respx.mock
async def test_retries_a_503_then_succeeds():
    route = respx.get("http://svc/x").mock(
        side_effect=[Response(503), Response(200, json={"ok": True})]
    )

    resp = await http.request("GET", "http://svc/x", timeout=5)

    assert resp.json() == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_gives_up_after_the_last_attempt():
    route = respx.get("http://svc/x").mock(return_value=Response(502))

    with pytest.raises(httpx.HTTPStatusError):
        await http.request("GET", "http://svc/x", timeout=5)

    assert route.call_count == http.ATTEMPTS


@respx.mock
async def test_does_not_retry_a_4xx():
    """A 4xx is a deterministic answer — including the poster's 429, which the
    worker handles as a scheduling decision rather than an error to hammer at."""
    route = respx.post("http://svc/schedule").mock(return_value=Response(429))

    with pytest.raises(httpx.HTTPStatusError):
        await http.request("POST", "http://svc/schedule", timeout=5)

    assert route.call_count == 1


@respx.mock
async def test_retries_a_dropped_connection():
    route = respx.get("http://svc/x").mock(
        side_effect=[httpx.ConnectError("boom"), Response(200, json={"ok": True})]
    )

    resp = await http.request("GET", "http://svc/x", timeout=5)

    assert resp.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_raises_the_transport_error_when_it_never_recovers():
    respx.get("http://svc/x").mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(httpx.ConnectError):
        await http.request("GET", "http://svc/x", timeout=5)
