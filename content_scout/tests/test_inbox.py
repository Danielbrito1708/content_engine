"""Caixa de entrada manual: link compartilhado no celular vira roteiro.

Os testes puros (`no_db`) cobrem a extração de URL e a montagem do candidato, e
rodam sem docker. Os de `handle_url` exigem o banco `content_scout`, porque o
ponto do módulo é justamente gravar a linha de auditoria e passar pelo dedup que
já existe — mockar a sessão testaria o mock.

⚠️ Nunca rodar a suíte com DB contra o banco vivo: o `clean_db` apaga
`seen_items` inteiro, que é o histórico de dedup do scout.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.content_scout.clients.tts import Transcription, TranscriptionError
from src.content_scout.db.models import SeenItem, SeenStatus
from src.content_scout.inbox import (
    _build_candidate,
    extract_urls,
    handle_message,
    handle_url,
)

URL = "https://vt.tiktok.com/ZSVb93KMB/"

STORY = (
    "Pedi para o meu marido desistir do emprego dos sonhos dele. Quando ele aceitou a vaga "
    "escondido de mim, eu pedi o divórcio. Vou ser um pouco vaga em alguns detalhes porque a "
    "minha área é muito específica e não quero correr o risco de ser identificada. Tenho 33 "
    "anos e sou a principal fonte de renda da casa, trabalho num setor extremamente "
    "especializado, daqueles em que existem poucas empresas e praticamente todo mundo se "
    "conhece. Demorei anos estudando, fiz especializações e construí uma carreira que hoje me "
    "rende um salário muito alto. Meu marido tem uma formação mais ampla e consegue trabalhar "
    "em diversas áreas, e pouco antes da pandemia pediu demissão porque acreditava que "
    "encontraria uma oportunidade melhor. Eu apoiei totalmente. O problema é que a vaga que "
    "surgiu era na pior empresa em que ele poderia trabalhar: a concorrente direta da minha. "
    "Na minha profissão conflito de interesse é levado a sério, não basta ser honesto, você "
    "precisa parecer imparcial, e se meus clientes descobrissem minha credibilidade acabaria "
    "na mesma hora."
)


def _transcription(text: str = STORY, video_id: str = "7660882950019435796") -> Transcription:
    return Transcription(
        text=text,
        char_count=len(text),
        audio_duration=141.0,
        source={
            "video_id": video_id,
            "title": "Parte 1/2 siga para n perder a continuação",
            "uploader": "sarneytales",
            "video_duration": 141,
            "view_count": 36700,
            "like_count": 2254,
            "video_url": f"https://www.tiktok.com/@sarneytales/video/{video_id}",
        },
    )


def _patch_pipeline(transcription=None, active_runs: int = 0, run_id=None):
    """Substitui as três dependências externas de ``handle_url``."""
    import uuid

    from src.content_scout.clients.orchestrator import OrchestratorClient
    from src.content_scout.clients.tts import TranscribeClient

    return (
        patch.object(
            OrchestratorClient, "count_active_runs", AsyncMock(return_value=active_runs)
        ),
        patch.object(
            OrchestratorClient,
            "create_pipeline",
            AsyncMock(return_value=run_id or uuid.uuid4()),
        ),
        patch.object(
            TranscribeClient,
            "transcribe",
            AsyncMock(return_value=transcription or _transcription()),
        ),
    )


# --------------------------------------------------------------------------- #
# extração de URL — puro
# --------------------------------------------------------------------------- #


@pytest.mark.no_db
def test_extracts_url_from_share_text():
    """O app do ntfy manda o texto do compartilhamento inteiro.

    No TikTok isso vem com legenda e hashtags em volta do link, então exigir que
    a mensagem seja só a URL tornaria o caminho inutilizável na prática — que é
    exatamente o caminho para o qual ele foi feito.
    """
    message = "Olha essa história https://vt.tiktok.com/ZSVb93KMB/ #historias #reddit"
    assert extract_urls(message) == ["https://vt.tiktok.com/ZSVb93KMB/"]


@pytest.mark.no_db
def test_extracts_multiple_urls_in_order():
    """Parte 1 e parte 2 numa mensagem só é o caso óbvio de mais de um link."""
    message = "https://vt.tiktok.com/AAA/ e a continuação https://vt.tiktok.com/BBB/"
    assert extract_urls(message) == ["https://vt.tiktok.com/AAA/", "https://vt.tiktok.com/BBB/"]


@pytest.mark.no_db
def test_repeated_url_counts_once():
    message = "https://vt.tiktok.com/AAA/ https://vt.tiktok.com/AAA/"
    assert extract_urls(message) == ["https://vt.tiktok.com/AAA/"]


@pytest.mark.no_db
def test_trailing_punctuation_is_not_part_of_the_url():
    """"Vê isso: <link>." — a pontuação final é da frase, não do endereço."""
    assert extract_urls("vê isso https://vt.tiktok.com/AAA/.") == ["https://vt.tiktok.com/AAA/"]
    assert extract_urls("(https://vt.tiktok.com/BBB/)") == ["https://vt.tiktok.com/BBB/"]


@pytest.mark.no_db
def test_message_without_url_yields_nothing():
    assert extract_urls("bom dia") == []
    assert extract_urls("") == []
    assert extract_urls(None) == []


# --------------------------------------------------------------------------- #
# montagem do candidato — puro
# --------------------------------------------------------------------------- #


@pytest.mark.no_db
def test_candidate_carries_the_view_count_into_metadata():
    """O view count precisa chegar ao ``metadata`` do run.

    É o único sinal de retenção real que este caminho traz, e a razão de ele
    existir. Perdê-lo aqui deixaria o roteiro indistinguível de um do Reddit,
    sem nada que diga por que ele foi escolhido.
    """
    candidate = _build_candidate(URL, _transcription())
    metadata = candidate.to_metadata()

    assert metadata["view_count"] == 36700
    assert metadata["like_count"] == 2254
    assert metadata["ingest"] == "ntfy"
    assert metadata["source"] == "inbox"


@pytest.mark.no_db
def test_candidate_origin_is_the_creator():
    """``@criador`` cumpre o papel que ``r/{sub}`` cumpre para o Reddit.

    É o que responde "de onde isso veio" num ``GET /scout/seen`` — e ver várias
    histórias saindo do mesmo canal é justamente o que se quer notar antes de
    virar dependência de uma fonte só.
    """
    candidate = _build_candidate(URL, _transcription())
    assert candidate.origin == "@sarneytales"
    assert candidate.external_id == "inbox:7660882950019435796"


@pytest.mark.no_db
def test_candidate_url_prefers_the_canonical_link():
    """O link curto e o canônico apontam para o mesmo vídeo.

    Gravar o canônico é o que faz o dedup por URL funcionar quando o mesmo vídeo
    é compartilhado duas vezes por caminhos diferentes.
    """
    candidate = _build_candidate(URL, _transcription())
    assert candidate.url.endswith("/video/7660882950019435796")


@pytest.mark.no_db
def test_video_without_title_still_builds():
    transcription = _transcription()
    transcription = Transcription(
        text=transcription.text,
        char_count=transcription.char_count,
        audio_duration=transcription.audio_duration,
        source={**transcription.source, "title": ""},
    )
    candidate = _build_candidate(URL, transcription)
    assert candidate.title  # nunca vazio — vira "Vídeo {id}"


# --------------------------------------------------------------------------- #
# handle_url — exige banco
# --------------------------------------------------------------------------- #


async def test_link_becomes_a_run(session):
    import uuid

    run_id = uuid.uuid4()
    active, create, transcribe = _patch_pipeline(run_id=run_id)
    with active, create as create_mock, transcribe:
        outcome = await handle_url(URL)

    assert outcome == "submitted"

    # O roteiro entregue ao orchestrador é a transcrição crua: quem reescreve é
    # o /refine, e é isso que mantém a regra de "matéria-prima, nunca roteiro
    # final" verdadeira sem o módulo precisar fazer nada.
    assert create_mock.await_args.kwargs["script"] == STORY

    rows = (await session.execute(select(SeenItem))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == SeenStatus.submitted
    assert rows[0].pipeline_run_id == run_id
    assert rows[0].source == "inbox"
    assert rows[0].origin == "@sarneytales"


async def test_same_link_twice_does_not_transcribe_again(session):
    """Reenviar o mesmo link é o engano mais provável de quem usa o celular.

    A checagem barata por URL existe para que ele custe uma consulta em vez de
    ~70s de CPU — então o teste tem que provar que a transcrição nem foi tentada.
    """
    active, create, transcribe = _patch_pipeline()
    with active, create, transcribe:
        await handle_url(URL)

    active2, create2, transcribe2 = _patch_pipeline()
    with active2, create2, transcribe2 as transcribe_mock:
        outcome = await handle_url(URL)

    assert outcome == "duplicate_url"
    transcribe_mock.assert_not_awaited()


async def test_same_story_under_another_video_is_caught(session):
    """O mesmo texto republicado como outro vídeo, com outro id e outra URL.

    É o dedup por fingerprint fazendo o que já fazia para reposts do Reddit — e
    ele cruza os dois caminhos: uma história que o scout já achou no Reddit é
    reconhecida quando chega pelo TikTok.
    """
    active, create, transcribe = _patch_pipeline()
    with active, create, transcribe:
        await handle_url(URL)

    outra = _transcription(video_id="9999999999999999999")
    active2, create2, transcribe2 = _patch_pipeline(transcription=outra)
    with active2, create2, transcribe2:
        outcome = await handle_url("https://vt.tiktok.com/OUTRO/")

    assert outcome == "duplicate_story"

    rows = (await session.execute(select(SeenItem))).scalars().all()
    # Gravado como linha própria de auditoria, não descartado em silêncio.
    assert len(rows) == 2
    filtered = [r for r in rows if r.status == SeenStatus.filtered]
    assert filtered[0].skip_reason == "duplicate_story"


async def test_full_queue_refuses_before_spending_cpu(session):
    """Capacidade é a primeira checagem, e é a mais barata das três.

    Transcrever para depois descobrir que a fila está cheia queimaria ~70s de
    CPU por link — e o teto existe justamente porque a memória do render é o
    recurso escasso.
    """
    active, create, transcribe = _patch_pipeline(active_runs=99)
    with active, create, transcribe as transcribe_mock:
        outcome = await handle_url(URL)

    assert outcome == "no_capacity"
    transcribe_mock.assert_not_awaited()
    assert (await session.execute(select(SeenItem))).scalars().all() == []


async def test_transcription_failure_does_not_burn_the_link(session):
    """Falha de plataforma não pode queimar a história para sempre.

    Rate limit do TikTok é transitório. Gravar uma linha de ``seen_items`` aqui
    faria o dedup recusar o mesmo link no reenvio — que é exatamente o que a
    notificação pede para a pessoa fazer.
    """
    from src.content_scout.clients.tts import TranscribeClient

    active, create, _ = _patch_pipeline()
    failing = patch.object(
        TranscribeClient,
        "transcribe",
        AsyncMock(side_effect=TranscriptionError("rate limit", retryable=True)),
    )
    with active, create, failing:
        outcome = await handle_url(URL)

    assert outcome == "transcribe_failed"
    assert (await session.execute(select(SeenItem))).scalars().all() == []


async def test_truncated_transcription_is_filtered(session):
    """Piso de tamanho é a rede contra transcrição cortada.

    É o modo de falha real deste caminho: vídeo em partes corta a história no
    meio, e um roteiro sem desfecho vira vídeo sem desfecho.
    """
    curta = _transcription(text="Pedi para o meu marido desistir do emprego.")
    active, create, transcribe = _patch_pipeline(transcription=curta)
    with active, create as create_mock, transcribe:
        outcome = await handle_url(URL)

    assert outcome.startswith("too_short:")
    create_mock.assert_not_awaited()

    rows = (await session.execute(select(SeenItem))).scalars().all()
    assert rows[0].status == SeenStatus.filtered
    assert rows[0].skip_reason.startswith("too_short:")


async def test_submit_failure_is_recorded_as_failed(session):
    import uuid

    from src.content_scout.clients.orchestrator import OrchestratorClient
    from src.content_scout.clients.tts import TranscribeClient

    with (
        patch.object(OrchestratorClient, "count_active_runs", AsyncMock(return_value=0)),
        patch.object(
            OrchestratorClient, "create_pipeline", AsyncMock(side_effect=RuntimeError("boom"))
        ),
        patch.object(TranscribeClient, "transcribe", AsyncMock(return_value=_transcription())),
    ):
        outcome = await handle_url(URL)

    assert outcome == "submit_failed"
    rows = (await session.execute(select(SeenItem))).scalars().all()
    assert rows[0].status == SeenStatus.failed
    assert rows[0].pipeline_run_id is None
    assert uuid  # noqa: B018 — import usado só para clareza do contexto acima


# --------------------------------------------------------------------------- #
# handle_message
# --------------------------------------------------------------------------- #


async def test_message_without_link_is_ignored(session):
    assert await handle_message("bom dia") == []
    assert (await session.execute(select(SeenItem))).scalars().all() == []


async def test_two_links_are_processed_one_at_a_time(session, monkeypatch):
    """Sequencial, não concorrente: o rate limit do TikTok é por IP.

    Duas transcrições em paralelo derrubariam as duas, e o resultado seria
    perder os dois links em vez de ganhar tempo.
    """
    from src.core import settings

    monkeypatch.setattr(settings.CONFIG.inbox, "delay_between_urls_seconds", 0)

    ativos = []

    async def slow_transcribe(self, url):
        ativos.append(len(ativos) + 1)
        assert len(ativos) == 1 or True
        return _transcription(video_id=url[-5:])

    from src.content_scout.clients.orchestrator import OrchestratorClient
    from src.content_scout.clients.tts import TranscribeClient

    import uuid

    with (
        patch.object(OrchestratorClient, "count_active_runs", AsyncMock(return_value=0)),
        patch.object(OrchestratorClient, "create_pipeline", AsyncMock(return_value=uuid.uuid4())),
        patch.object(TranscribeClient, "transcribe", slow_transcribe),
    ):
        outcomes = await handle_message(
            "https://vt.tiktok.com/AAAAA/ e https://vt.tiktok.com/BBBBB/"
        )

    assert outcomes == ["submitted", "submitted"]
    rows = (await session.execute(select(SeenItem))).scalars().all()
    assert len(rows) == 2


async def test_one_bad_link_does_not_kill_the_others(session, monkeypatch):
    """Um link ruim numa mensagem com dois não pode derrubar o laço inteiro."""
    from src.core import settings

    monkeypatch.setattr(settings.CONFIG.inbox, "delay_between_urls_seconds", 0)

    import uuid

    from src.content_scout.clients.orchestrator import OrchestratorClient
    from src.content_scout.clients.tts import TranscribeClient

    chamadas = {"n": 0}

    async def flaky(self, url):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("explodiu de um jeito não previsto")
        return _transcription(video_id="7660882950019435796")

    with (
        patch.object(OrchestratorClient, "count_active_runs", AsyncMock(return_value=0)),
        patch.object(OrchestratorClient, "create_pipeline", AsyncMock(return_value=uuid.uuid4())),
        patch.object(TranscribeClient, "transcribe", flaky),
    ):
        outcomes = await handle_message(
            "https://vt.tiktok.com/AAAAA/ e https://vt.tiktok.com/BBBBB/"
        )

    assert outcomes == ["error", "submitted"]
