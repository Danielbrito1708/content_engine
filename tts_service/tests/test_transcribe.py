"""POST /transcribe: URL de vídeo → texto corrido para o refino reescrever.

Nada aqui toca a rede nem o whisper de verdade. O download é mockado no ponto
mais fundo que ainda deixa a lógica sob teste — ``_extract``, que é a única
função do módulo que fala com o yt-dlp — para que a escolha de formato, o
fallback de api_hostname e o teto de duração continuem sendo exercitados.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.tts_service.audio.download import DownloadError, DownloadedMedia, download_audio

FAKE_SOURCE = {
    "video_id": "7660882950019435796",
    "title": "Parte 1/2 siga para n perder a continuação",
    "uploader": "sarneytales",
    "video_duration": 141,
    "view_count": 36700,
    "like_count": 2254,
    "video_url": "https://www.tiktok.com/@sarneytales/video/7660882950019435796",
}

FAKE_TEXT = "Pedi para o meu marido desistir do emprego dos sonhos dele."


def _fake_info(duration: float = 141.0) -> dict:
    return {
        "id": FAKE_SOURCE["video_id"],
        "title": FAKE_SOURCE["title"],
        "uploader": FAKE_SOURCE["uploader"],
        "duration": duration,
        "view_count": FAKE_SOURCE["view_count"],
        "like_count": FAKE_SOURCE["like_count"],
        "webpage_url": FAKE_SOURCE["video_url"],
    }


def _patch_route(text: str = FAKE_TEXT, duration: float = 141.0):
    """Substitui as duas metades caras da rota: baixar e transcrever."""
    media = DownloadedMedia(
        path=Path("/tmp/fake.mp3"),
        video_id=FAKE_SOURCE["video_id"],
        title=FAKE_SOURCE["title"],
        uploader=FAKE_SOURCE["uploader"],
        duration=float(FAKE_SOURCE["video_duration"]),
        view_count=FAKE_SOURCE["view_count"],
        like_count=FAKE_SOURCE["like_count"],
        webpage_url=FAKE_SOURCE["video_url"],
    )
    return (
        patch("src.tts_service.api.routes.transcribe.download_audio", return_value=media),
        patch(
            "src.tts_service.api.routes.transcribe.transcribe_file_to_text",
            return_value=(text, duration),
        ),
    )


# --------------------------------------------------------------------------- #
# rota
# --------------------------------------------------------------------------- #


async def test_transcribe_returns_text_and_source(client):
    download, transcribe = _patch_route()
    with download, transcribe:
        resp = await client.post("/transcribe", json={"url": "https://vt.tiktok.com/ZSVb93KMB/"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["text"] == FAKE_TEXT
    assert data["char_count"] == len(FAKE_TEXT)
    assert data["audio_duration"] == 141.0
    assert data["source"] == FAKE_SOURCE


async def test_view_count_survives_the_round_trip(client):
    """O view count é a razão de existir deste caminho, não um extra.

    É o único sinal de retenção real que o pipeline recebe — o upvote do Reddit
    diz quantos votaram, a visualização diz que a história prendeu. Se ele se
    perder entre o yt-dlp e a resposta, o caminho inteiro fica sem o dado que o
    justifica, e nada mais no sistema perceberia.
    """
    download, transcribe = _patch_route()
    with download, transcribe:
        resp = await client.post("/transcribe", json={"url": "https://vt.tiktok.com/x/"})

    assert resp.json()["source"]["view_count"] == 36700
    assert resp.json()["source"]["like_count"] == 2254


async def test_download_failure_is_502_not_400(client):
    """A URL está bem formada; quem recusou foi a plataforma.

    A distinção não é cosmética: a resposta ao rate limit do TikTok é reenviar
    mais tarde, e a resposta a uma URL inválida não é. O ``content_scout`` lê
    justamente esse código para decidir se avisa "tente de novo".
    """
    failing = patch(
        "src.tts_service.api.routes.transcribe.download_audio",
        side_effect=DownloadError("Unexpected response from webpage request"),
    )
    with failing:
        resp = await client.post("/transcribe", json={"url": "https://vt.tiktok.com/x/"})

    assert resp.status_code == 502
    assert "Download error" in resp.json()["detail"]


async def test_empty_transcription_is_422(client):
    """Áudio sem fala é um resultado, não uma falha de infraestrutura.

    Mas devolver 200 com texto vazio faria o chamador criar um run de roteiro em
    branco, que só morreria lá na frente — e mais caro, depois de TTS e render.
    """
    download, transcribe = _patch_route(text="")
    with download, transcribe:
        resp = await client.post("/transcribe", json={"url": "https://vt.tiktok.com/x/"})

    assert resp.status_code == 422
    assert "fala" in resp.json()["detail"]


async def test_whitespace_only_transcription_is_422(client):
    download, transcribe = _patch_route(text="   ")
    with download, transcribe:
        resp = await client.post("/transcribe", json={"url": "https://vt.tiktok.com/x/"})
    # Um retorno só de espaço vem de áudio sem fala igual, e não pode virar
    # roteiro em branco só por não ser a string vazia.
    assert resp.status_code == 422


async def test_url_is_required(client):
    resp = await client.post("/transcribe", json={})
    assert resp.status_code == 422


async def test_empty_url_is_rejected_before_any_download(client):
    """Validação de schema, não 502: aqui a culpa é de quem chamou."""
    resp = await client.post("/transcribe", json={"url": ""})
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #


def test_download_uses_impersonation(tmp_path):
    """Sem impersonation o TikTok responde 200 com uma casca vazia.

    É o obstáculo nº 1 e o mais fácil de perder numa refatoração, porque o
    yt-dlp aceita a opção sem reclamar e o sintoma aparece como erro de
    extractor, não como opção faltando.
    """
    captured = {}

    def fake_extract(url, opts):
        captured.update(opts)
        (tmp_path / "video.mp3").write_bytes(b"fake")
        return _fake_info()

    with patch("src.tts_service.audio.download._extract", side_effect=fake_extract):
        media = download_audio("https://vt.tiktok.com/x/", tmp_path)

    assert captured["impersonate"] is not None
    assert captured["format"] == "ba/b"
    assert captured["noplaylist"] is True
    assert media.video_id == FAKE_SOURCE["video_id"]
    assert media.view_count == 36700


def test_mobile_api_is_the_second_attempt_never_the_first(tmp_path):
    """O fallback resolve vídeos que o parser da página recusa.

    Mas é o caminho menos estável dos dois, então não pode virar o padrão: a
    primeira tentativa tem que sair sem ``extractor_args``, e a segunda com.
    """
    calls = []

    def fake_extract(url, opts):
        calls.append(opts.get("extractor_args"))
        if len(calls) == 1:
            raise RuntimeError("Unable to extract universal data for rehydration")
        (tmp_path / "video.mp3").write_bytes(b"fake")
        return _fake_info()

    with patch("src.tts_service.audio.download._extract", side_effect=fake_extract):
        media = download_audio(
            "https://vt.tiktok.com/x/", tmp_path, api_hostname="api16-normal-c-useast1a.tiktokv.com"
        )

    assert calls[0] is None, "a primeira tentativa não pode usar a API mobile"
    assert calls[1] == {"tiktok": {"api_hostname": ["api16-normal-c-useast1a.tiktokv.com"]}}
    assert media.video_id == FAKE_SOURCE["video_id"]


def test_no_fallback_configured_means_one_attempt(tmp_path):
    calls = []

    def fake_extract(url, opts):
        calls.append(opts)
        raise RuntimeError("Unexpected response from webpage request")

    with patch("src.tts_service.audio.download._extract", side_effect=fake_extract):
        with pytest.raises(DownloadError):
            download_audio("https://vt.tiktok.com/x/", tmp_path, api_hostname="")

    assert len(calls) == 1


def test_duration_ceiling_rejects_before_transcribing(tmp_path):
    """O teto existe porque o custo está na transcrição, não no download.

    A ~0.4x tempo real, um vídeo longo mandado por engano queimaria minutos de
    CPU antes de alguém notar. Por isso o corte é aqui e não depois.
    """

    def fake_extract(url, opts):
        (tmp_path / "video.mp3").write_bytes(b"fake")
        return _fake_info(duration=4000.0)

    with patch("src.tts_service.audio.download._extract", side_effect=fake_extract):
        with pytest.raises(DownloadError, match="teto"):
            download_audio("https://vt.tiktok.com/x/", tmp_path, max_duration_seconds=1800)


def test_duration_ceiling_off_accepts_anything(tmp_path):
    def fake_extract(url, opts):
        (tmp_path / "video.mp3").write_bytes(b"fake")
        return _fake_info(duration=4000.0)

    with patch("src.tts_service.audio.download._extract", side_effect=fake_extract):
        media = download_audio("https://vt.tiktok.com/x/", tmp_path, max_duration_seconds=0)

    assert media.duration == 4000.0


def test_silent_success_without_a_file_is_an_error(tmp_path):
    """yt-dlp saindo 0 sem gravar arquivo aconteceu de verdade.

    ``--print`` implica ``--simulate``: o metadata sai correto, o download não
    acontece, e o processo termina com sucesso. Tratar isso como sucesso faria o
    whisper receber um caminho inexistente e falhar bem mais longe da causa.
    """

    def fake_extract(url, opts):
        return _fake_info()

    with patch("src.tts_service.audio.download._extract", side_effect=fake_extract):
        with pytest.raises(DownloadError, match="não gravou arquivo"):
            download_audio("https://vt.tiktok.com/x/", tmp_path)


def test_partial_downloads_are_not_mistaken_for_the_audio(tmp_path):
    def fake_extract(url, opts):
        (tmp_path / "video.mp3.part").write_bytes(b"incompleto")
        return _fake_info()

    with patch("src.tts_service.audio.download._extract", side_effect=fake_extract):
        with pytest.raises(DownloadError, match="não gravou arquivo"):
            download_audio("https://vt.tiktok.com/x/", tmp_path)


def test_missing_metadata_does_not_crash(tmp_path):
    """O yt-dlp omite campos conforme a plataforma; ``None`` é 'não informado'.

    Zero seria a afirmação de que o vídeo não teve visualização, que é outra
    coisa — e é a leitura que faria a comparação de desempenho mentir depois.
    """

    def fake_extract(url, opts):
        (tmp_path / "video.mp3").write_bytes(b"fake")
        return {"id": "abc", "duration": 10}

    with patch("src.tts_service.audio.download._extract", side_effect=fake_extract):
        media = download_audio("https://vt.tiktok.com/x/", tmp_path)

    assert media.view_count is None
    assert media.like_count is None
    assert media.uploader == ""
    assert media.as_metadata()["view_count"] is None


# --------------------------------------------------------------------------- #
# cache de modelos
# --------------------------------------------------------------------------- #


def test_model_cache_is_keyed_by_name():
    """Dois modelos coexistem: ``base`` para legenda, ``small`` para roteiro.

    O cache era um slot único, e com ele o segundo chamador receberia
    silenciosamente o modelo do primeiro — legenda de render rodando no modelo
    grande, ou transcrição de vídeo rodando no ``base``, que erra demais com
    música por baixo. Nenhum dos dois falharia de forma visível.
    """
    from src.tts_service.audio import transcribe as module

    created = []

    class FakeModel:
        def __init__(self, name, **kwargs):
            created.append(name)

    module._models.clear()
    with patch.object(module, "WhisperModel", FakeModel):
        first = module._get_model("base")
        second = module._get_model("small")
        again = module._get_model("base")

    assert created == ["base", "small"], "cada nome carrega uma vez"
    assert first is again, "o mesmo nome reusa a instância"
    assert first is not second, "nomes diferentes não podem colidir"
    module._models.clear()
