"""Tests for the Azure Speech provider. httpx is always mocked — no network."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.config import TTSEnvSettings
from src.tts_service.tts.azure import AzureTTSClient, build_ssml, voice_locale

_ENV = dict(
    AZURE_SPEECH_KEY="secret-key",
    AZURE_SPEECH_REGION="brazilsouth",
    TTS_VOICE="pt-BR-ThalitaNeural",
    TTS_RATE="+15%",
)


def _client(**overrides) -> AzureTTSClient:
    """Builds a client without touching the bootstrapped global settings."""
    defaults = dict(
        key="secret-key",
        region="brazilsouth",
        voice="pt-BR-ThalitaNeural",
        rate="+15%",
        output_format="audio-48khz-192kbitrate-mono-mp3",
    )
    return AzureTTSClient(**{**defaults, **overrides})


def _mock_post(status_code=200, content=b"\xff\xfb\x90\x00audio", headers=None):
    response = httpx.Response(
        status_code=status_code,
        content=content,
        headers=headers or {},
        request=httpx.Request("POST", "https://brazilsouth.tts.speech.microsoft.com/"),
    )
    return patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=response))


# --- locale derivation ---

def test_locale_from_voice_name():
    assert voice_locale("pt-BR-ThalitaNeural") == "pt-BR"
    assert voice_locale("en-US-JennyNeural") == "en-US"


def test_locale_falls_back_for_unexpected_voice_shape():
    assert voice_locale("weird") == "pt-BR"


# --- SSML ---

def test_ssml_contains_voice_and_rate():
    ssml = build_ssml("Olá mundo", "pt-BR-ThalitaNeural", "+15%")
    assert "name='pt-BR-ThalitaNeural'" in ssml
    assert "rate='+15%'" in ssml
    assert "Olá mundo" in ssml


def test_ssml_uses_locale_derived_from_voice():
    ssml = build_ssml("hi", "en-US-JennyNeural", "+0%")
    assert "xml:lang='en-US'" in ssml
    assert "pt-BR" not in ssml


def test_ssml_escapes_xml_special_characters():
    # A script line with & or < would produce malformed SSML and a 400 from Azure.
    ssml = build_ssml("Tom & Jerry <3", "pt-BR-ThalitaNeural", "+0%")
    assert "Tom &amp; Jerry &lt;3" in ssml
    assert "Tom & Jerry" not in ssml


# --- request construction ---

async def test_generate_returns_audio_bytes():
    with _mock_post(content=b"MP3BYTES") as mock:
        result = await _client().generate("olá")
    assert result == b"MP3BYTES"
    assert mock.await_count == 1


async def test_generate_posts_to_region_endpoint():
    with _mock_post() as mock:
        await _client(region="eastus").generate("olá")
    url = mock.await_args[0][0]
    assert url == "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"


async def test_generate_sends_subscription_key_and_output_format():
    with _mock_post() as mock:
        await _client().generate("olá")
    headers = mock.await_args.kwargs["headers"]
    assert headers["Ocp-Apim-Subscription-Key"] == "secret-key"
    assert headers["X-Microsoft-OutputFormat"] == "audio-48khz-192kbitrate-mono-mp3"
    assert headers["Content-Type"] == "application/ssml+xml"
    # Azure rejects requests without a User-Agent.
    assert headers["User-Agent"]


async def test_generate_sends_ssml_body_as_utf8():
    with _mock_post() as mock:
        await _client().generate("coração")
    body = mock.await_args.kwargs["content"]
    assert isinstance(body, bytes)
    assert "coração" in body.decode("utf-8")


async def test_output_format_is_overridable():
    with _mock_post() as mock:
        await _client(output_format="riff-24khz-16bit-mono-pcm").generate("olá")
    assert mock.await_args.kwargs["headers"]["X-Microsoft-OutputFormat"] == "riff-24khz-16bit-mono-pcm"


# --- failure modes ---

async def test_generate_raises_on_http_error():
    with _mock_post(status_code=401, content=b"unauthorized"):
        with pytest.raises(RuntimeError, match="401"):
            await _client().generate("olá")


async def test_generate_raises_on_empty_body():
    # A 200 with no audio would otherwise upload a zero-byte MP3 to MinIO.
    with _mock_post(content=b""):
        with pytest.raises(RuntimeError, match="empty audio"):
            await _client().generate("olá")


# --- config wiring ---

def test_azure_requires_key(monkeypatch):
    for k, v in {**_ENV, "TTS_PROVIDER": "azure"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AZURE_SPEECH_KEY")
    with pytest.raises(ValueError, match="AZURE_SPEECH_KEY"):
        TTSEnvSettings()


def test_azure_requires_region(monkeypatch):
    for k, v in {**_ENV, "TTS_PROVIDER": "azure"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AZURE_SPEECH_REGION")
    with pytest.raises(ValueError, match="AZURE_SPEECH_REGION"):
        TTSEnvSettings()


def test_azure_defaults_to_48khz_192kbps(monkeypatch):
    for k, v in {**_ENV, "TTS_PROVIDER": "azure"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AZURE_OUTPUT_FORMAT", raising=False)
    assert TTSEnvSettings().azure_output_format == "audio-48khz-192kbitrate-mono-mp3"


def test_edge_provider_does_not_require_azure_keys(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "edge")
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    assert TTSEnvSettings().tts_provider == "edge"


def test_factory_returns_azure_client(monkeypatch):
    from src.tts_service.tts import factory
    monkeypatch.setattr(
        factory,
        "settings",
        SimpleNamespace(env=SimpleNamespace(tts_provider="azure")),
    )
    with patch("src.tts_service.tts.azure.settings") as mock_settings:
        mock_settings.env = SimpleNamespace(
            azure_speech_key="k",
            azure_speech_region="brazilsouth",
            tts_voice="pt-BR-ThalitaNeural",
            tts_rate="+15%",
            azure_output_format="audio-48khz-192kbitrate-mono-mp3",
        )
        client = factory.get_tts_client()
    assert isinstance(client, AzureTTSClient)
