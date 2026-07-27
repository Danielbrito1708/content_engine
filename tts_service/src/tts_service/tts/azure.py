from xml.sax.saxutils import escape

import httpx

from src.core import settings
from src.tts_service.tts.base import BaseTTSClient

_ENDPOINT = "https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"

# Azure needs a User-Agent; it rejects the request without one.
_USER_AGENT = "content-engine-tts"

_DEFAULT_LOCALE = "pt-BR"


def voice_locale(voice: str) -> str:
    """'pt-BR-ThalitaNeural' -> 'pt-BR'. Falls back to pt-BR for unexpected shapes."""
    parts = voice.split("-")
    if len(parts) >= 3:
        return f"{parts[0]}-{parts[1]}"
    return _DEFAULT_LOCALE


def build_ssml(text: str, voice: str, rate: str) -> str:
    """SSML body for the Azure REST endpoint.

    `rate` is the same signed-percentage string the edge provider uses, so switching
    providers keeps the narration pacing identical.
    """
    locale = voice_locale(voice)
    return (
        f"<speak version='1.0' xml:lang='{locale}'>"
        f"<voice xml:lang='{locale}' name='{voice}'>"
        f"<prosody rate='{rate}'>{escape(text)}</prosody>"
        f"</voice></speak>"
    )


class AzureTTSClient(BaseTTSClient):
    """Azure Speech (Cognitive Services) REST TTS.

    Same neural voices as the edge provider, but the output format is ours to pick.
    edge-tts is locked to audio-24khz-48kbitrate-mono-mp3 — 24 kHz means nothing above
    ~12 kHz survives, which is what made the narration sound muffled. Here the default
    is 48 kHz / 192 kbps.
    """

    def __init__(
        self,
        key: str | None = None,
        region: str | None = None,
        voice: str | None = None,
        rate: str | None = None,
        output_format: str | None = None,
        timeout: float = 60.0,
    ):
        self._key = key or settings.env.azure_speech_key
        self._region = region or settings.env.azure_speech_region
        self._voice = voice or settings.env.tts_voice
        self._rate = rate or settings.env.tts_rate
        self._output_format = output_format or settings.env.azure_output_format
        self._timeout = timeout

    @property
    def _url(self) -> str:
        return _ENDPOINT.format(region=self._region)

    async def generate(self, text: str) -> bytes:
        headers = {
            "Ocp-Apim-Subscription-Key": self._key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self._output_format,
            "User-Agent": _USER_AGENT,
        }
        ssml = build_ssml(text, self._voice, self._rate)

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(self._url, headers=headers, content=ssml.encode("utf-8"))

        if resp.status_code != 200:
            # Azure puts the reason in a header on 4xx; the body is usually empty.
            reason = resp.headers.get("X-Microsoft-OutputFormat-Error") or resp.text
            raise RuntimeError(
                f"Azure TTS returned {resp.status_code}: {reason[:300] or resp.reason_phrase}"
            )

        if not resp.content:
            raise RuntimeError("Azure TTS returned an empty audio body")

        return resp.content
