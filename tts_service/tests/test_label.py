"""`label`: nomear o arquivo quando o áudio não é uma parte do roteiro.

Existe porque a frase gancho é narrada sozinha e precisa de key própria — sem
isso ela sobrescreveria `part_1.mp3`, que é a narração da parte inteira.
"""
from unittest.mock import AsyncMock, patch

from tests.conftest import FAKE_MP3, SAMPLE_REQUEST

RUN_ID = SAMPLE_REQUEST["run_id"]


def _mock_transcription():
    return (
        patch("src.tts_service.api.routes.generate.transcribe_to_srt", return_value=b"1\n"),
        patch("src.tts_service.api.routes.generate.upload_bytes", new_callable=AsyncMock),
    )


async def _post(client, payload):
    transcribe, upload_srt = _mock_transcription()
    with (
        patch("src.tts_service.api.routes.generate.get_tts_client") as mock_factory,
        patch("src.tts_service.api.routes.generate.upload_audio", new_callable=AsyncMock),
        transcribe,
        upload_srt,
    ):
        mock_factory.return_value.generate = AsyncMock(return_value=FAKE_MP3)
        return await client.post("/generate", json=payload)


async def test_label_names_both_keys(client):
    resp = await _post(client, {**SAMPLE_REQUEST, "label": "hook"})

    assert resp.status_code == 201
    assert resp.json()["audio_key"] == f"audio/{RUN_ID}/hook.mp3"
    assert resp.json()["srt_key"] == f"subs/{RUN_ID}/hook.srt"


async def test_label_wins_over_part_number(client):
    """Com label, o part_number não entra na key — ele só existe por
    compatibilidade com quem ainda manda o campo."""
    resp = await _post(client, {**SAMPLE_REQUEST, "part_number": 7, "label": "hook"})

    assert resp.json()["audio_key"] == f"audio/{RUN_ID}/hook.mp3"


async def test_without_label_keys_are_unchanged(client):
    resp = await _post(client, SAMPLE_REQUEST)

    assert resp.json()["audio_key"] == f"audio/{RUN_ID}/part_1.mp3"
    assert resp.json()["srt_key"] == f"subs/{RUN_ID}/part_1.srt"


async def test_null_label_falls_back_to_part(client):
    resp = await _post(client, {**SAMPLE_REQUEST, "label": None})

    assert resp.json()["audio_key"] == f"audio/{RUN_ID}/part_1.mp3"


async def test_part_number_is_optional(client):
    payload = {k: v for k, v in SAMPLE_REQUEST.items() if k != "part_number"}
    resp = await _post(client, {**payload, "label": "hook"})

    assert resp.status_code == 201
    assert resp.json()["audio_key"] == f"audio/{RUN_ID}/hook.mp3"


async def test_label_with_path_separator_is_rejected(client):
    """A key é montada por interpolação: `../` escreveria fora do run."""
    resp = await _post(client, {**SAMPLE_REQUEST, "label": "../../etc/passwd"})

    assert resp.status_code == 422


async def test_label_with_slash_is_rejected(client):
    resp = await _post(client, {**SAMPLE_REQUEST, "label": "hook/v2"})

    assert resp.status_code == 422


async def test_label_with_uppercase_is_rejected(client):
    resp = await _post(client, {**SAMPLE_REQUEST, "label": "Hook"})

    assert resp.status_code == 422


async def test_empty_label_is_rejected(client):
    resp = await _post(client, {**SAMPLE_REQUEST, "label": ""})

    assert resp.status_code == 422


async def test_label_accepts_dash_and_underscore(client):
    resp = await _post(client, {**SAMPLE_REQUEST, "label": "hook_v2-final"})

    assert resp.status_code == 201
    assert resp.json()["audio_key"] == f"audio/{RUN_ID}/hook_v2-final.mp3"
