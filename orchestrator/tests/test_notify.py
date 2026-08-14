"""O módulo de notificação.

O que está sendo protegido aqui não é o envio da mensagem — é a promessa de que
o monitoramento nunca derruba nem atrasa o que ele monitora. Um alerta perdido
custa um diagnóstico; um alerta que estoura dentro do `run_pipeline` custa o
vídeo, e custa justamente no caminho que existe para avisar que algo deu errado.
"""

import asyncio
import json
from datetime import datetime, timezone

import pytest
import respx
from httpx import Response

from src.core import notify as notify_module
from src.core.notify import (
    QUEUE_MAX,
    _deliver,
    format_message,
    notify,
    ping,
    sender_loop,
    short_id,
)
from src.orchestrator.db.models import PipelinePart, PipelineRun
from src.orchestrator.worker import _schedule, _when, run_pipeline

@pytest.fixture
def webhook(monkeypatch):
    """Liga o destino webhook e devolve a URL."""
    url = "http://notify.test/hook"
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", url)
    return url


@pytest.fixture
def whatsapp(monkeypatch):
    """Liga o destino CallMeBot apontado para um host de teste."""
    monkeypatch.setenv("CALLMEBOT_PHONE", "5511987654321")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "123456")
    monkeypatch.setenv("CALLMEBOT_BASE_URL", "http://callmebot.test/whatsapp.php")


def _queued() -> list[str]:
    queue = notify_module._get_queue()
    return [queue.get_nowait() for _ in range(queue.qsize())]


# ── formatação ───────────────────────────────────────────────────────

def test_format_message_without_fields():
    assert format_message("Run concluído", icon="🚀") == "🚀 Run concluído"


def test_format_message_puts_details_on_a_second_line():
    message = format_message("Vídeo renderizado", icon="🎬", run="4f3a91c2", parte="2/3")

    assert message == "🎬 Vídeo renderizado\nrun: 4f3a91c2 · parte: 2/3"


def test_format_message_drops_empty_fields():
    """Quase todo evento tem campo opcional; mostrá-los vazios enche a mensagem
    de ruído exatamente onde a tela é estreita."""
    message = format_message("Roteiro refinado", run="abc", gancho=None, narrador="")

    assert message == "• Roteiro refinado\nrun: abc"


def test_format_message_keeps_zero():
    """`0` é falsy mas é informação — "na fila: 0" é diferente de não saber."""
    assert "na_fila: 0" in format_message("Fila", na_fila=0)


def test_format_message_truncates():
    """O CallMeBot é um GET: a mensagem inteira vai na query string, e um stack
    trace de 4 KB estouraria a URL."""
    message = format_message("x", erro="e" * 5000)

    assert len(message) == notify_module.MAX_TEXT


def test_short_id_keeps_eight_chars():
    assert short_id("4f3a91c2-1234-5678-9abc-def012345678") == "4f3a91c2"


# ── enfileiramento e níveis ──────────────────────────────────────────

def test_notify_is_noop_without_any_destination():
    """Sem credencial nenhuma o módulo não pode encher a fila: ninguém drenaria."""
    notify("qualquer coisa")

    assert notify_module._get_queue().qsize() == 0


def test_notify_enqueues_with_webhook(webhook):
    notify("Run concluído", icon="🚀", run="abc")

    assert _queued() == ["🚀 Run concluído\nrun: abc"]


def test_notify_enqueues_with_whatsapp(whatsapp):
    notify("Run concluído")

    assert len(_queued()) == 1


def test_notify_requires_both_callmebot_vars(monkeypatch):
    """Telefone sem apikey não é destino — o GET sairia e seria recusado."""
    monkeypatch.setenv("CALLMEBOT_PHONE", "5511987654321")

    notify("Run concluído")

    assert notify_module._get_queue().qsize() == 0


def test_notify_drops_below_the_configured_level(webhook, monkeypatch):
    monkeypatch.setattr(notify_module, "_cfg", lambda key, default: "info" if key == "level" else default)

    notify("miúdo", level="debug")
    notify("marco", level="info")

    assert _queued() == ["• marco"]


def test_notify_always_lets_errors_through(webhook, monkeypatch):
    monkeypatch.setattr(notify_module, "_cfg", lambda key, default: "warning" if key == "level" else default)

    notify("falhou", level="error", icon="❌")

    assert _queued() == ["❌ falhou"]


def test_notify_respects_the_enabled_switch(webhook, monkeypatch):
    monkeypatch.setattr(notify_module, "_cfg", lambda key, default: False if key == "enabled" else default)

    notify("qualquer coisa")

    assert notify_module._get_queue().qsize() == 0


def test_notify_survives_a_full_queue(webhook):
    """Perder notificação é aceitável; travar o pipeline atrás dela não é."""
    for i in range(QUEUE_MAX):
        notify(f"mensagem {i}")

    notify("a que sobra de fora")  # não pode levantar

    assert notify_module._get_queue().qsize() == QUEUE_MAX


def test_notify_never_raises(webhook, monkeypatch):
    """A garantia central do módulo, testada no ponto onde ela é possível de
    violar: qualquer coisa que estoure lá dentro é engolida."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("destino explodiu")

    monkeypatch.setattr(notify_module, "_enabled", boom)

    notify("qualquer coisa")  # não pode levantar


def test_notify_missing_monitoring_section_uses_defaults(webhook, monkeypatch):
    """Um serviço cujo config.ini ainda não tem `[monitoring]` continua subindo."""
    monkeypatch.delattr(notify_module.settings.CONFIG, "monitoring", raising=False)

    notify("Run concluído")

    assert len(_queued()) == 1


# ── entrega ──────────────────────────────────────────────────────────

@respx.mock
async def test_deliver_sends_to_callmebot_urlencoded(whatsapp):
    route = respx.get("http://callmebot.test/whatsapp.php").mock(return_value=Response(200))

    await _deliver("🚀 Run concluído\nrun: abc")

    assert route.called
    params = route.calls[0].request.url.params
    assert params["phone"] == "5511987654321"
    assert params["apikey"] == "123456"
    assert params["text"] == "🚀 Run concluído\nrun: abc"


@respx.mock
async def test_deliver_posts_json_to_webhook(webhook):
    route = respx.post(webhook).mock(return_value=Response(200))

    await _deliver("🚀 Run concluído")

    assert route.called
    assert json.loads(route.calls[0].request.read()) == {"text": "🚀 Run concluído"}


@respx.mock
async def test_deliver_uses_both_destinations_when_both_are_set(whatsapp, webhook):
    """Os dois podem conviver — é o caminho de migração para WAHA sem apagar o
    CallMeBot antes de saber que o novo funciona."""
    wa = respx.get("http://callmebot.test/whatsapp.php").mock(return_value=Response(200))
    hook = respx.post(webhook).mock(return_value=Response(200))

    await _deliver("mensagem")

    assert wa.called and hook.called


@respx.mock
async def test_deliver_swallows_a_dead_destination(webhook, monkeypatch):
    monkeypatch.setattr(notify_module, "SEND_ATTEMPTS", 1)
    respx.post(webhook).mock(return_value=Response(500))

    await _deliver("mensagem")  # não pode levantar


@respx.mock
async def test_deliver_retries_once(webhook, monkeypatch):
    monkeypatch.setattr(notify_module, "SEND_BACKOFF", 0)
    route = respx.post(webhook).mock(side_effect=[Response(500), Response(200)])

    await _deliver("mensagem")

    assert route.call_count == 2


# ── dead-man's switch ────────────────────────────────────────────────

async def test_ping_is_noop_without_the_env_var():
    await ping("produced")  # não pode levantar nem bater em rede


@respx.mock
async def test_ping_hits_the_configured_url(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_PRODUCED_URL", "http://hc.test/uuid-produced")
    route = respx.get("http://hc.test/uuid-produced").mock(return_value=Response(200))

    await ping("produced")

    assert route.called


@respx.mock
async def test_ping_appends_fail(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_ALIVE_URL", "http://hc.test/uuid-alive")
    route = respx.get("http://hc.test/uuid-alive/fail").mock(return_value=Response(200))

    await ping("alive", fail=True)

    assert route.called


@respx.mock
async def test_ping_swallows_failure(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_ALIVE_URL", "http://hc.test/uuid-alive")
    respx.get("http://hc.test/uuid-alive").mock(return_value=Response(500))

    await ping("alive")  # não pode levantar


# ── o sender ─────────────────────────────────────────────────────────

@respx.mock
async def test_sender_loop_drains_the_queue(webhook, monkeypatch):
    monkeypatch.setattr(notify_module, "_cfg", lambda key, default: 0 if key == "min_interval_seconds" else default)
    route = respx.post(webhook).mock(return_value=Response(200))

    notify("primeira")
    notify("segunda")

    task = asyncio.create_task(sender_loop())
    await notify_module._get_queue().join()
    task.cancel()

    assert route.call_count == 2


# ── os enganches no pipeline ─────────────────────────────────────────

async def _run_with_one_rendered_part(session):
    run = PipelineRun(raw_script="Roteiro.", parts_count=1)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    session.add(
        PipelinePart(run_id=run.id, part_number=1, script="Parte.", video_key="outputs/x.mp4")
    )
    await session.commit()
    return run


@respx.mock
async def test_failed_run_reports_the_stage_it_died_in(session, webhook):
    """O estágio é lido antes de `status = failed`.

    Sem isso toda mensagem de falha diria "etapa: failed", que é verdade e não
    serve para nada — o estágio é a única pista na mensagem sobre onde procurar.
    """
    run = PipelineRun(raw_script="Roteiro.")
    session.add(run)
    await session.commit()
    await session.refresh(run)
    respx.post("http://llm_service:8000/refine").mock(return_value=Response(500))

    await run_pipeline(run.id)

    assert any("Run falhou" in m and "etapa: refining" in m for m in _queued())


@respx.mock
async def test_schedule_pings_produced_on_success(session, monkeypatch):
    """O dead-man's switch de produto: pingado no único ponto que significa
    "saiu vídeo de verdade"."""
    monkeypatch.setenv("HEALTHCHECK_PRODUCED_URL", "http://hc.test/produced")
    run = await _run_with_one_rendered_part(session)
    respx.post("http://tiktok_poster:8000/schedule").mock(
        return_value=Response(200, json={
            "scheduled_at": "2025-06-01T08:00:00Z", "buffer_update_id": "buf_x",
        })
    )
    hc = respx.get("http://hc.test/produced").mock(return_value=Response(200))

    assert await _schedule(session, run) is True
    assert hc.called


@respx.mock
async def test_schedule_does_not_ping_produced_when_the_queue_is_full(session, monkeypatch):
    """A metade que importa mais: um run que ficou esperando vaga **não** produziu
    nada. Pingar aqui faria o switch afirmar que o sistema está entregando
    justamente enquanto ele parou — e um dead-man's switch que mente é pior que
    não ter nenhum, porque compra silêncio."""
    monkeypatch.setenv("HEALTHCHECK_PRODUCED_URL", "http://hc.test/produced")
    run = await _run_with_one_rendered_part(session)
    respx.post("http://tiktok_poster:8000/schedule").mock(
        return_value=Response(429, json={"detail": {"pending_count": 10}})
    )
    hc = respx.get("http://hc.test/produced").mock(return_value=Response(200))

    assert await _schedule(session, run) is False
    assert not hc.called


def test_when_formats_in_the_local_timezone():
    moment = datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc)

    assert _when(moment) == moment.astimezone().strftime("%d/%m %H:%M")


def test_when_tolerates_a_missing_schedule():
    """Parte sem `scheduled_at` é possível — o campo some da mensagem em vez de
    virar "para: None"."""
    assert _when(None) is None


@respx.mock
async def test_sender_loop_survives_a_failed_send(webhook, monkeypatch):
    """A mensagem seguinte tem que sair mesmo depois de um destino fora do ar —
    senão uma falha de rede derruba o canal de alerta em silêncio."""
    monkeypatch.setattr(notify_module, "_cfg", lambda key, default: 0 if key == "min_interval_seconds" else default)
    monkeypatch.setattr(notify_module, "SEND_ATTEMPTS", 1)
    route = respx.post(webhook).mock(side_effect=[Response(500), Response(200)])

    notify("a que falha")
    notify("a que precisa sair mesmo assim")

    task = asyncio.create_task(sender_loop())
    await notify_module._get_queue().join()
    task.cancel()

    assert route.call_count == 2
