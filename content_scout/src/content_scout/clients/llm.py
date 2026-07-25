from dataclasses import dataclass

import httpx

from src.core import settings


@dataclass(frozen=True)
class Verdict:
    safe: bool
    category: str | None = None
    reason: str | None = None

    def as_skip_reason(self) -> str:
        return f"unsafe:{self.category or 'unspecified'}"


class ModerationError(RuntimeError):
    """The verdict could not be obtained.

    Deliberately distinct from an unsafe verdict: "we could not check" must never
    collapse into either "safe" (publishes unchecked content) or "unsafe"
    (permanently discards a good story over a transient outage).
    """


class ModerationClient:
    def __init__(self):
        self._base = settings.CONFIG.services.llm_url
        self._timeout = settings.CONFIG.scout.moderation_timeout

    async def check(self, title: str, text: str) -> Verdict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/moderate", json={"title": title, "text": text}
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ModerationError(str(exc)) from exc

        return Verdict(
            safe=bool(data.get("safe")),
            category=data.get("category"),
            reason=data.get("reason"),
        )
