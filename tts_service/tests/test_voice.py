"""A narração sai na voz do gênero de quem conta a história.

Mesmos mocks de `test_rate.py`: nenhuma chamada real ao TTS, e os testes de
endpoint olham o que a rota pediu à factory — não precisam do 201.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from src.core import settings
from src.core.config import DEFAULT_FEMALE_VOICE, DEFAULT_MALE_VOICE, TTSEnvSettings
from src.tts_service.tts.voices import normalize_gender, resolve_voice
from tests.conftest import FAKE_MP3, SAMPLE_REQUEST

VOICES = {"default": "pt-BR-ThalitaNeural", "male": "pt-BR-AntonioNeural",
          "female": "pt-BR-FranciscaNeural"}


# ── normalize_gender ─────────────────────────────────────────────


@pytest.mark.parametrize("value", ["male", "MALE", " Male ", "female", "unknown"])
def test_known_genders_survive_normalization(value):
    assert normalize_gender(value) == value.strip().lower()


@pytest.mark.parametrize("value", [None, "", "masculino", "m", "nao-binario", "all"])
def test_anything_unrecognised_is_unknown(value):
    """Vem de uma classificação de LLM dois serviços acima: um valor estranho
    devolve a voz de sempre, não um erro."""
    assert normalize_gender(value) == "unknown"


# ── resolve_voice ────────────────────────────────────────────────


def test_male_narrator_gets_the_male_voice():
    assert resolve_voice("male", **VOICES) == "pt-BR-AntonioNeural"


def test_female_narrator_gets_the_female_voice():
    assert resolve_voice("female", **VOICES) == "pt-BR-FranciscaNeural"


@pytest.mark.parametrize("value", [None, "unknown", "", "qualquer coisa"])
def test_narrator_without_gender_keeps_the_configured_voice(value):
    """`TTS_VOICE` é o que todo vídeo usou antes disso existir — é para lá que
    uma história sem narrador identificável volta."""
    assert resolve_voice(value, **VOICES) == "pt-BR-ThalitaNeural"


def test_resolution_is_case_insensitive():
    assert resolve_voice("Female", **VOICES) == "pt-BR-FranciscaNeural"


# ── config ───────────────────────────────────────────────────────


def test_default_voices_are_the_pt_br_pair():
    assert settings.env.tts_voice_male == "pt-BR-AntonioNeural"
    assert settings.env.tts_voice_female == "pt-BR-FranciscaNeural"


def test_voices_read_from_env(monkeypatch):
    monkeypatch.setenv("TTS_VOICE_MALE", "pt-BR-DonatoNeural")
    monkeypatch.setenv("TTS_VOICE_FEMALE", "pt-BR-ThalitaNeural")
    env = TTSEnvSettings()
    assert env.tts_voice_male == "pt-BR-DonatoNeural"
    assert env.tts_voice_female == "pt-BR-ThalitaNeural"


def test_voices_default_when_env_absent(monkeypatch):
    monkeypatch.delenv("TTS_VOICE_MALE", raising=False)
    monkeypatch.delenv("TTS_VOICE_FEMALE", raising=False)
    env = TTSEnvSettings()
    assert env.tts_voice_male == DEFAULT_MALE_VOICE
    assert env.tts_voice_female == DEFAULT_FEMALE_VOICE


# ── narrator_gender por request ──────────────────────────────────


def _patched_endpoint():
    return (
        patch("src.tts_service.api.routes.generate.get_tts_client"),
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
    )


async def _voice_asked_for(client, body):
    factory_p, upload_p = _patched_endpoint()
    with factory_p as mock_factory, upload_p:
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        await client.post("/generate", json=body)
    return mock_factory.call_args.kwargs["voice"]


async def test_male_request_narrates_with_the_male_voice(client):
    voice = await _voice_asked_for(client, {**SAMPLE_REQUEST, "narrator_gender": "male"})
    assert voice == settings.env.tts_voice_male


async def test_female_request_narrates_with_the_female_voice(client):
    voice = await _voice_asked_for(client, {**SAMPLE_REQUEST, "narrator_gender": "female"})
    assert voice == settings.env.tts_voice_female


async def test_absent_gender_keeps_the_configured_voice(client):
    """Quem chama o serviço direto, ou um orchestrador antigo, continua com a voz
    de sempre — deploy dos dois serviços não é atômico."""
    voice = await _voice_asked_for(client, SAMPLE_REQUEST)
    assert voice == settings.env.tts_voice


async def test_explicit_null_gender_keeps_the_configured_voice(client):
    voice = await _voice_asked_for(client, {**SAMPLE_REQUEST, "narrator_gender": None})
    assert voice == settings.env.tts_voice


async def test_unrecognised_gender_does_not_fail_the_request(client):
    """Um valor esquisito custa a voz certa, nunca o run inteiro."""
    factory_p, upload_p = _patched_endpoint()
    with factory_p as mock_factory, upload_p:
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        resp = await client.post(
            "/generate", json={**SAMPLE_REQUEST, "narrator_gender": "masculino"}
        )

    assert resp.status_code != 422
    assert mock_factory.call_args.kwargs["voice"] == settings.env.tts_voice


async def test_response_includes_the_resolved_voice(client):
    """O orchestrador precisa saber qual voz narrou, para guardar no run e
    repassar ao tiktok_poster como variante de teste A/B."""
    from tests.test_generate import _mock_transcription

    factory_p, upload_p = _patched_endpoint()
    transcribe, upload_srt = _mock_transcription()
    with factory_p as mock_factory, upload_p, transcribe, upload_srt:
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        resp = await client.post(
            "/generate", json={**SAMPLE_REQUEST, "narrator_gender": "male"}
        )

    assert resp.status_code == 201
    assert resp.json()["voice"] == settings.env.tts_voice_male


async def test_gender_and_rate_travel_together(client):
    """As duas decisões da narração saem na mesma chamada: gancho e partes têm de
    bater em voz *e* em ritmo, senão soam como dois narradores."""
    factory_p, upload_p = _patched_endpoint()
    with factory_p as mock_factory, upload_p:
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        await client.post(
            "/generate",
            json={**SAMPLE_REQUEST, "narrator_gender": "male", "rate": "+20%"},
        )

    kwargs = mock_factory.call_args.kwargs
    assert kwargs["voice"] == settings.env.tts_voice_male
    assert kwargs["rate"] == "+20%"


# ── factory ──────────────────────────────────────────────────────


def test_factory_forwards_voice(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "edge")
    from src.tts_service.tts.factory import get_tts_client
    assert get_tts_client(voice="pt-BR-AntonioNeural")._voice == "pt-BR-AntonioNeural"


def test_factory_without_voice_uses_env(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "edge")
    from src.tts_service.tts.factory import get_tts_client
    assert get_tts_client()._voice == settings.env.tts_voice


def test_factory_forwards_voice_to_azure():
    """A voz do narrador tem de sobreviver à troca de provider, não só funcionar
    no edge."""
    from types import SimpleNamespace

    from src.tts_service.tts import factory

    stub = SimpleNamespace(env=SimpleNamespace(tts_provider="azure"))
    with patch.object(factory, "settings", stub):
        client = factory.get_tts_client(voice="pt-BR-AntonioNeural")

    assert client._voice == "pt-BR-AntonioNeural"


def test_azure_ssml_carries_the_voice():
    from src.tts_service.tts.azure import build_ssml
    ssml = build_ssml("olá", "pt-BR-AntonioNeural", "+15%")
    assert "name='pt-BR-AntonioNeural'" in ssml


async def test_edge_receives_the_voice():
    from unittest.mock import MagicMock

    from src.tts_service.tts.edge import EdgeTTSClient

    async def stream():
        yield {"type": "audio", "data": b"\xff\xfb"}

    instance = MagicMock()
    instance.stream = stream

    with patch("src.tts_service.tts.edge.edge_tts.Communicate") as mock_comm:
        mock_comm.return_value = instance
        await EdgeTTSClient(voice="pt-BR-AntonioNeural").generate("olá")

    assert mock_comm.call_args.args[1] == "pt-BR-AntonioNeural"


# ── health ───────────────────────────────────────────────────────


async def test_health_exposes_both_gendered_voices(client):
    body = (await client.get("/health")).json()
    assert body["voice_male"] == os.environ.get("TTS_VOICE_MALE", DEFAULT_MALE_VOICE)
    assert body["voice_female"] == os.environ.get("TTS_VOICE_FEMALE", DEFAULT_FEMALE_VOICE)
