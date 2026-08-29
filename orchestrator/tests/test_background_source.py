"""Materialização de fundo sob demanda: união dos candidatos, queda e despejo."""

import uuid

from src.orchestrator.background_source import evict
from src.orchestrator.db.models import PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.worker import _ensure_background, background_key_for

PREFIX = "assets/backgrounds/"

MANIFESTO = {
    "clips": [
        {"key": f"{PREFIX}vid_{s:05d}.mp4", "video_id": "vid", "start": s}
        for s in (0, 120, 240)
    ]
}


async def _run(session):
    run = PipelineRun(raw_script="Roteiro.", status=PipelineStatus.processing, classification={})
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _parte(session, run_id, numero, *, fundo=None, video_key="outputs/x.mp4"):
    parte = PipelinePart(
        run_id=run_id,
        part_number=numero,
        script=f"Parte {numero}.",
        background_key=fundo,
        video_key=video_key,
    )
    session.add(parte)
    await session.commit()
    return parte


# ── candidatos: manifesto ∪ bucket ─────────────────────────────────────────

async def test_candidatos_unem_manifesto_e_bucket(session, monkeypatch):
    """Um segmento ainda não materializado concorre em pé de igualdade com um
    clipe subido à mão — senão uma biblioteca esconderia a outra."""
    async def fake_list(_bucket, _prefix):
        return [f"{PREFIX}asmr_000.mp4"]

    monkeypatch.setattr("src.orchestrator.worker.list_keys", fake_list)

    escolhidos = {
        await background_key_for(session, uuid.UUID(int=i), 1, MANIFESTO) for i in range(40)
    }
    assert f"{PREFIX}asmr_000.mp4" in escolhidos
    assert any(k.startswith(f"{PREFIX}vid_") for k in escolhidos)


async def test_manifesto_vazio_mantem_o_comportamento_antigo(session, monkeypatch):
    """Manifesto ausente faz a rotação voltar a sortear só o que está publicado:
    a biblioteca materializada é camada a mais, não pré-requisito."""
    async def fake_list(_bucket, _prefix):
        return [f"{PREFIX}asmr_000.mp4", f"{PREFIX}asmr_001.mp4"]

    monkeypatch.setattr("src.orchestrator.worker.list_keys", fake_list)

    for i in range(10):
        assert (await background_key_for(session, uuid.UUID(int=i), 1, {})).startswith(
            f"{PREFIX}asmr_"
        )


# ── _ensure_background ─────────────────────────────────────────────────────

async def test_clipe_fora_do_manifesto_nao_e_materializado(session, monkeypatch):
    """Clipe subido à mão já está no bucket por definição."""
    def explode(*_a, **_k):
        raise AssertionError("não deveria tentar materializar")

    monkeypatch.setattr("src.orchestrator.worker.ensure_available", explode)

    chave = f"{PREFIX}asmr_000.mp4"
    assert await _ensure_background(session, chave, MANIFESTO) == chave


async def test_falha_ao_baixar_cai_para_outro_clipe(session, monkeypatch):
    """Download depende de rede e de site de terceiro. Um soluço ali não pode
    custar um run que já pagou LLM, TTS e Whisper."""
    async def falha(*_a, **_k):
        raise RuntimeError("yt-dlp: HTTP 403")

    async def fake_list(_bucket, _prefix):
        return [f"{PREFIX}asmr_000.mp4", f"{PREFIX}asmr_001.mp4"]

    monkeypatch.setattr("src.orchestrator.worker.ensure_available", falha)
    monkeypatch.setattr("src.orchestrator.worker.list_keys", fake_list)

    usada = await _ensure_background(session, f"{PREFIX}vid_00120.mp4", MANIFESTO)

    assert usada.startswith(f"{PREFIX}asmr_")
    assert usada != f"{PREFIX}vid_00120.mp4"


async def test_falha_sem_nenhum_clipe_disponivel_usa_o_fundo_fixo(session, monkeypatch):
    async def falha(*_a, **_k):
        raise RuntimeError("sem rede")

    async def vazio(_bucket, _prefix):
        return []

    monkeypatch.setattr("src.orchestrator.worker.ensure_available", falha)
    monkeypatch.setattr("src.orchestrator.worker.list_keys", vazio)

    assert await _ensure_background(session, f"{PREFIX}vid_00000.mp4", MANIFESTO) == (
        "assets/background.mp4"
    )


async def test_despejo_so_roda_depois_de_materializar(session, monkeypatch):
    """Varrer o prefixo a cada render custaria uma listagem por vídeo sem nada
    ter mudado — o cache só pode ter estourado logo após um download."""
    chamadas = []

    async def ja_existia(*_a, **_k):
        return False

    async def espiao(*_a, **_k):
        chamadas.append(1)
        return []

    monkeypatch.setattr("src.orchestrator.worker.ensure_available", ja_existia)
    monkeypatch.setattr("src.orchestrator.worker.evict", espiao)

    await _ensure_background(session, f"{PREFIX}vid_00000.mp4", MANIFESTO)
    assert chamadas == []


# ── despejo LRU ────────────────────────────────────────────────────────────

async def test_evict_nao_faz_nada_abaixo_do_teto(session):
    apagados = await evict(session, bucket="b", materializados=[f"{PREFIX}a.mp4"], teto=60)
    assert apagados == []


async def test_evict_apaga_o_menos_recentemente_usado(session, monkeypatch):
    apagados = []

    async def fake_delete(_bucket, keys):
        apagados.extend(keys)

    monkeypatch.setattr("src.orchestrator.background_source.delete_keys", fake_delete)

    run = await _run(session)
    velho, novo = f"{PREFIX}velho.mp4", f"{PREFIX}novo.mp4"
    await _parte(session, run.id, 1, fundo=velho)
    await _parte(session, run.id, 2, fundo=novo)

    resultado = await evict(session, bucket="b", materializados=[velho, novo], teto=1)

    assert resultado == [velho]
    assert apagados == [velho]


async def test_evict_nunca_apaga_clipe_de_parte_que_ainda_nao_renderizou(session, monkeypatch):
    """Parte com `background_key` gravado e `video_key` nulo está esperando o
    render, e o blender_worker vai buscar essa chave no bucket. Apagá-la trocaria
    o freio de espaço por um render quebrado."""
    monkeypatch.setattr(
        "src.orchestrator.background_source.delete_keys",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("não deveria apagar")),
    )

    run = await _run(session)
    pendente = f"{PREFIX}pendente.mp4"
    await _parte(session, run.id, 1, fundo=pendente, video_key=None)

    assert await evict(session, bucket="b", materializados=[pendente], teto=0) == []


async def test_evict_nao_apaga_o_recem_materializado(session, monkeypatch):
    """Clipe sem parte nenhuma apontando para ele é o do render que está
    acontecendo agora — despejá-lo apagaria o fundo recém-buscado."""
    apagados = []

    async def fake_delete(_bucket, keys):
        apagados.extend(keys)

    monkeypatch.setattr("src.orchestrator.background_source.delete_keys", fake_delete)

    run = await _run(session)
    usado, recem = f"{PREFIX}usado.mp4", f"{PREFIX}recem.mp4"
    await _parte(session, run.id, 1, fundo=usado)

    resultado = await evict(session, bucket="b", materializados=[usado, recem], teto=1)

    assert resultado == [usado]
    assert recem not in apagados


async def test_objeto_que_nao_e_video_nunca_vira_fundo(session, monkeypatch):
    """Um `.json` solto sob o prefixo entraria no sorteio e o render morreria
    montando-o como movie strip."""
    async def com_lixo(_bucket, _prefix):
        return [f"{PREFIX}manifest.json", f"{PREFIX}leiame.txt", f"{PREFIX}asmr_000.mp4"]

    monkeypatch.setattr("src.orchestrator.worker.list_keys", com_lixo)

    escolhidos = {
        await background_key_for(session, uuid.UUID(int=i), 1, {}) for i in range(20)
    }
    assert escolhidos == {f"{PREFIX}asmr_000.mp4"}
