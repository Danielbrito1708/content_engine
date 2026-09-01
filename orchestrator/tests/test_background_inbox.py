"""Caixa de entrada de fundos: link de vídeo compartilhado no celular vira
clipe novo no manifesto. Todos puros/mockados — nenhum yt-dlp, nenhuma rede,
nenhum bucket de verdade envolvido.
"""

import json

import pytest

from src.orchestrator.background_inbox import (
    extract_urls,
    handle_message,
    handle_url,
)

URL = "https://www.youtube.com/watch?v=abc123"


def _manifest(clips=None):
    return {"version": 1, "segment_seconds": 120, "clips": clips or []}


def _patch_manifest(monkeypatch, manifest):
    uploaded = {}

    async def fake_get_bytes(_bucket, _key):
        return json.dumps(manifest).encode("utf-8")

    async def fake_upload_bytes(_bucket, _key, data, content_type=None):
        uploaded["manifest"] = json.loads(data)
        uploaded["content_type"] = content_type

    monkeypatch.setattr("src.orchestrator.background_inbox.get_bytes", fake_get_bytes)
    monkeypatch.setattr("src.orchestrator.background_inbox.upload_bytes", fake_upload_bytes)
    return uploaded


def _patch_metadata(monkeypatch, info):
    def fake(_url):
        return info

    monkeypatch.setattr("src.orchestrator.background_inbox._fetch_metadata", fake)


# ── extract_urls (puro) ──────────────────────────────────────────────────

def test_extract_urls_extrai_de_texto_solto():
    assert extract_urls(f"olha esse vídeo {URL} muito bom") == [URL]


def test_extract_urls_sem_link_devolve_vazio():
    assert extract_urls("sem link nenhum aqui") == []


def test_extract_urls_nao_repete():
    assert extract_urls(f"{URL} de novo {URL}") == [URL]


def test_extract_urls_ignora_pontuacao_no_fim():
    assert extract_urls(f"olha: {URL}.") == [URL]


# ── handle_url ───────────────────────────────────────────────────────────

async def test_video_novo_vira_clipes_no_manifesto(monkeypatch):
    _patch_metadata(monkeypatch, {"id": "abc123", "duration": 250, "title": "Gameplay"})
    uploaded = _patch_manifest(monkeypatch, _manifest())

    resultado = await handle_url(URL)

    assert resultado == "submitted"
    chaves = {c["key"] for c in uploaded["manifest"]["clips"]}
    # 250s / 120s = 2 inteiros; sobra de 10s < min_tail (45s), então descartada.
    assert len(chaves) == 2
    assert all("abc123" in c for c in chaves)


async def test_video_ja_no_manifesto_e_duplicado(monkeypatch):
    from src.orchestrator.backgrounds import segment_key

    prefix = "assets/backgrounds/"
    existentes = [
        {"key": segment_key(prefix, "abc123", 0), "video_id": "abc123", "start": 0},
        {"key": segment_key(prefix, "abc123", 120), "video_id": "abc123", "start": 120},
    ]
    _patch_metadata(monkeypatch, {"id": "abc123", "duration": 250, "title": "Gameplay"})

    async def fake_get_bytes(_bucket, _key):
        return json.dumps(_manifest(existentes)).encode("utf-8")

    def explode(*_a, **_k):
        raise AssertionError("vídeo já coberto não deveria reenviar o manifesto")

    monkeypatch.setattr("src.orchestrator.background_inbox.get_bytes", fake_get_bytes)
    monkeypatch.setattr("src.orchestrator.background_inbox.upload_bytes", explode)

    resultado = await handle_url(URL)

    assert resultado == "duplicate"


async def test_video_curto_demais_nao_produz_clipe(monkeypatch):
    _patch_metadata(monkeypatch, {"id": "abc123", "duration": 20, "title": "Clipe curto"})

    resultado = await handle_url(URL)

    assert resultado == "too_short"


async def test_sem_duracao_no_metadado(monkeypatch):
    _patch_metadata(monkeypatch, {"id": "abc123", "title": "Sem duração"})

    resultado = await handle_url(URL)

    assert resultado == "no_duration"


async def test_yt_dlp_falha_nao_derruba_o_laço(monkeypatch):
    def explode(_url):
        raise RuntimeError("Unsupported URL")

    monkeypatch.setattr("src.orchestrator.background_inbox._fetch_metadata", explode)

    resultado = await handle_url(URL)

    assert resultado == "metadata_failed"


# ── handle_message ───────────────────────────────────────────────────────

async def test_handle_message_sem_url():
    assert await handle_message("mensagem qualquer, sem link") == []


async def test_handle_message_processa_cada_url(monkeypatch):
    outros = "https://www.youtube.com/watch?v=zzz999"

    def fake(url):
        vid = "abc123" if "abc123" in url else "zzz999"
        return {"id": vid, "duration": 20, "title": "curto"}

    monkeypatch.setattr("src.orchestrator.background_inbox._fetch_metadata", fake)

    resultados = await handle_message(f"{URL} e também {outros}")

    assert resultados == ["too_short", "too_short"]
