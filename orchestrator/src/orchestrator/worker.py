import asyncio
import json
import uuid
from datetime import datetime

from sqlalchemy import func, select
from structlog import get_logger

from src.core import settings
from src.core.notify import notify, ping, short_id
from src.orchestrator.background_source import ensure_available, evict
from src.orchestrator.backgrounds import (
    is_clip,
    manifest_entry,
    manifest_keys,
    pick_background,
)
from src.orchestrator.clients.blender import BlenderClient
from src.orchestrator.clients.llm import LLMClient
from src.orchestrator.clients.tiktok import BufferQueueFull, TikTokClient
from src.orchestrator.clients.tts import TTSClient
from src.orchestrator.db.engine import AsyncSessionLocal
from src.orchestrator.db.models import PartStatus, PipelinePart, PipelineRun, PipelineStatus
from src.orchestrator.hook_text import opens_with_hook
from src.orchestrator.storage.client import get_bytes, list_keys

log = get_logger(__name__)

#: Nome do arquivo do gancho dentro do run: `audio/{run_id}/hook.mp3`.
HOOK_LABEL = "hook"

#: Guide de layout do card, resolvido pelo blender_worker em `templates/`.
#: Sobrescrevível em `config.ini [template] card_template`.
DEFAULT_CARD_TEMPLATE = "comment_default"

#: States that mean "work is supposed to be happening". The scout counts these as
#: occupied capacity, so nothing may sit in one of them without an owner.
ACTIVE_STATUSES = (
    PipelineStatus.pending,
    PipelineStatus.refining,
    PipelineStatus.refined,
    PipelineStatus.processing,
    PipelineStatus.scheduling,
)


async def run_pipeline(run_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(PipelineRun, run_id)
        if run is None:
            log.error("pipeline_run not found", run_id=str(run_id))
            return

        try:
            await _refine(session, run)
            # Read once, before the first TTS call: the hook is narrated *into*
            # the video now, so it has to be spoken at the same rate as the
            # narration it introduces — a hook at a different speed reads as a
            # different voice.
            rate = await _narration_rate(run)
            await _run_hook_tts(session, run, rate)
            await _render_card(session, run)
            await _process_all_parts(session, run, rate)
            await _schedule(session, run)
        except Exception as exc:
            # Lido antes da atribuição: depois dela todo run falha "em failed", e
            # o estágio é a única coisa na mensagem que diz onde procurar.
            stage = run.status.value
            run.status = PipelineStatus.failed
            run.error = str(exc)
            log.error("pipeline failed", run_id=str(run_id), error=str(exc))
            notify(
                "Run falhou",
                level="error",
                icon="❌",
                run=short_id(run_id),
                etapa=stage,
                erro=str(exc)[:200],
            )
            await session.commit()


async def _refine(session, run: PipelineRun) -> None:
    log.info("refining script", run_id=str(run.id))
    notify("Refinando roteiro", level="debug", icon="🧠", run=short_id(run.id))
    run.status = PipelineStatus.refining
    await session.commit()

    result = await LLMClient().refine(run.raw_script, run.input_metadata or {})

    run.refined_script = "\n\n".join(result.parts)
    run.hook = result.hook or None
    run.narrator_gender = result.narrator_gender
    run.youtube_title = result.youtube_title or None
    run.classification = result.classification
    run.parts_count = len(result.parts)
    run.status = PipelineStatus.refined
    await session.commit()

    for i, script in enumerate(result.parts, start=1):
        session.add(PipelinePart(run_id=run.id, part_number=i, script=script))
    await session.commit()

    log.info(
        "script refined",
        run_id=str(run.id),
        parts=run.parts_count,
        hook=bool(run.hook),
        narrator=run.narrator_gender,
        title=bool(run.youtube_title),
    )
    notify(
        "Roteiro refinado",
        icon="✂️",
        run=short_id(run.id),
        partes=run.parts_count,
        narrador=run.narrator_gender,
        gancho=(run.hook or "")[:120] or None,
    )


async def _run_hook_tts(session, run: PipelineRun, rate: str | None = None) -> None:
    """Narra a frase gancho num arquivo próprio, separado das partes.

    Esse áudio abre o vídeo: ele é montado sobre o card, e a narração da parte
    só começa quando ele termina. Vai com o mesmo `rate` **e o mesmo
    `narrator_gender`** das partes — o gancho e a narração que ele apresenta são
    a mesma pessoa, e basta a voz ou a velocidade divergir para soarem como duas.

    Não derruba o run em caso de falha: a intro é uma camada a mais sobre um
    vídeo que já se sustenta sem ela (a narração da parte 1 abre com essa mesma
    frase), e perder a camada não pode custar o vídeo inteiro, que é o que o
    pipeline existe para entregar. A ausência fica visível em `hook_audio_key`
    nulo, e o render volta a começar pela narração.
    """
    if not run.hook:
        log.info("no hook returned by refine, skipping hook audio", run_id=str(run.id))
        return

    log.info(
        "generating hook audio",
        run_id=str(run.id),
        chars=len(run.hook),
        rate=rate,
        narrator=run.narrator_gender,
    )

    try:
        audio_key, srt_key = await TTSClient().generate(
            text=run.hook,
            run_id=str(run.id),
            label=HOOK_LABEL,
            rate=rate,
            narrator_gender=run.narrator_gender,
        )
    except Exception as exc:
        log.warning("hook audio failed, continuing without it", run_id=str(run.id), error=str(exc))
        # Nível `warning`, e não `debug` como o sucesso: o vídeo continua e o run
        # termina como qualquer outro, então esta é uma das degradações que nenhum
        # status de run denuncia. Se não avisar aqui, não avisa em lugar nenhum.
        notify(
            "Gancho não pôde ser narrado — vídeo sai sem a intro falada",
            level="warning",
            icon="⚠️",
            run=short_id(run.id),
            erro=str(exc)[:200],
        )
        return

    run.hook_audio_key = audio_key
    run.hook_srt_key = srt_key
    await session.commit()
    log.info("hook audio ready", run_id=str(run.id), audio=audio_key, srt=srt_key)
    notify("Gancho narrado", level="debug", icon="🎙️", run=short_id(run.id))


async def _render_card(session, run: PipelineRun) -> None:
    """Compõe o card de comentário com a frase gancho, mostrado na intro.

    Degradável pelo mesmo motivo do áudio do gancho: card é a camada visual da
    intro, e um vídeo sem ela ainda é o vídeo. A key nula é o que o render lê
    como "não há card".
    """
    if not run.hook:
        log.info("no hook, skipping card", run_id=str(run.id))
        return

    template = str(getattr(settings.CONFIG.template, "card_template", "") or DEFAULT_CARD_TEMPLATE)
    output_key = f"cards/{run.id}.png"

    try:
        card_key = await BlenderClient().render_card(
            text=run.hook,
            template=template,
            output_key=output_key,
        )
    except Exception as exc:
        log.warning("card render failed, continuing without it", run_id=str(run.id), error=str(exc))
        # Mesma razão do gancho: degrada o vídeo sem marcar o run.
        notify(
            "Card da intro falhou — vídeo sai sem a abertura visual",
            level="warning",
            icon="⚠️",
            run=short_id(run.id),
            erro=str(exc)[:200],
        )
        return

    run.card_key = card_key
    await session.commit()
    log.info("card ready", run_id=str(run.id), card=card_key)
    notify("Card da intro pronto", level="debug", icon="🖼️", run=short_id(run.id))


def _template_id_for(run: PipelineRun) -> uuid.UUID:
    """The template this run renders with — the run's override, or the deploy default.

    Called both here and by `_run_render`, which have to agree: this reads
    `narration.rate` before the first TTS, and if the TTS ran at one template's
    rate while the render used another, the narration would come out too fast
    or too slow for the cut the video actually gets.
    """
    return run.template_id or uuid.UUID(settings.env.blender_template_id)


async def _narration_rate(run: PipelineRun) -> str | None:
    """`narration.rate` from the template, or None to let the tts_service decide.

    A template without a narration block — or an unreachable blender_worker — is not
    worth failing a run over: the pipeline falls back to the tts_service's TTS_RATE.
    """
    template_id = _template_id_for(run)
    try:
        config = await BlenderClient().get_template_config(template_id)
    except Exception as exc:
        log.warning("could not read template config, using tts_service default rate", error=str(exc))
        # Outra que não marca o run: a narração sai na velocidade errada e o
        # vídeo publica normalmente. Só se descobre ouvindo.
        notify(
            "Template ilegível — narração sai na velocidade default do tts_service",
            level="warning",
            icon="⚠️",
            erro=str(exc)[:200],
        )
        return None

    rate = (config.get("narration") or {}).get("rate")
    if rate is None:
        log.info("template has no narration.rate, using tts_service default")
    return rate


async def _process_all_parts(session, run: PipelineRun, rate: str | None = None) -> None:
    run.status = PipelineStatus.processing
    await session.commit()

    for part in await _parts_of(session, run):
        await _run_tts(session, part, run, rate)
        await _run_render(session, part, run)

    log.info("all parts processed", run_id=str(run.id))


async def _run_tts(session, part: PipelinePart, run: PipelineRun, rate: str | None = None) -> None:
    log.info(
        "generating audio",
        run_id=str(run.id),
        part=part.part_number,
        rate=rate,
        narrator=run.narrator_gender,
    )
    part.status = PartStatus.tts_running
    await session.commit()

    audio_key, srt_key = await TTSClient().generate(
        text=part.script,
        run_id=str(run.id),
        part_number=part.part_number,
        rate=rate,
        # Do run, não da parte: uma história dividida é a mesma pessoa contando.
        narrator_gender=run.narrator_gender,
    )

    part.audio_key = audio_key
    part.srt_key = srt_key
    part.status = PartStatus.tts_done
    await session.commit()
    log.info("audio ready", run_id=str(run.id), part=part.part_number, audio=audio_key, srt=srt_key)
    notify(
        "Narração pronta",
        level="debug",
        icon="🔊",
        run=short_id(run.id),
        parte=f"{part.part_number}/{run.parts_count}",
    )


def _hook_is_muted(part: PipelinePart, run: PipelineRun) -> bool:
    """Se o áudio do gancho entra só como duração, sem ser tocado.

    A parte 1 abre pela frase gancho — é de lá que ela é copiada. Tocar o
    arquivo do gancho na frente dessa narração faria o vídeo dizer a mesma frase
    duas vezes seguidas, nos segundos em que a retenção se decide. Então nessa
    parte quem narra a frase é a narração inteira, como sempre foi, e o áudio
    separado serve só para o render saber por quanto tempo o card fica na tela.

    Nas partes 2+ o gancho não está na narração, e aí o arquivo é tocado de
    verdade: é o que faz todas as partes da série abrirem igual.

    A condição é o texto da parte, não o número dela — se um dia o refino
    devolver o gancho no começo da parte 2, ela se comporta como a parte 1
    sozinha.
    """
    return bool(run.hook) and opens_with_hook(part.script, run.hook)


async def _background_usage(session) -> dict[str, int]:
    """Quantas partes já saíram em cada clipe.

    É a memória da rotação. Conta a tabela inteira, não uma janela: o objetivo é
    "nenhum clipe repete antes de a biblioteca acabar", e isso é uma contagem
    acumulada. Partes anteriores à coluna têm `background_key` nulo e ficam de
    fora — o primeiro ciclo depois da migration passa pela biblioteca inteira.
    """
    rows = await session.execute(
        select(PipelinePart.background_key, func.count())
        .where(PipelinePart.background_key.is_not(None))
        .group_by(PipelinePart.background_key)
    )
    return {key: count for key, count in rows.all()}


async def _load_background_manifest() -> dict:
    """O manifesto de segmentos materializáveis, ou ``{}``.

    Vive no bucket e não no repo pela mesma razão que o `template.json`: a lista
    de fontes muda sem deploy. Manifesto ausente ou ilegível devolve ``{}``, e a
    rotação volta a sortear só entre os arquivos já publicados — a biblioteca
    materializada é uma camada a mais, não um pré-requisito.
    """
    path = str(getattr(settings.CONFIG.template, "background_manifest", "") or "")
    if not path:
        return {}
    try:
        return json.loads(await get_bytes(settings.CONFIG.storage.bucket, path))
    except Exception as exc:  # noqa: BLE001 — botocore e json levantam famílias diferentes
        log.warning("background manifest unreadable", path=path, error=str(exc)[:200])
        return {}


async def background_key_for(
    session, run_id: uuid.UUID, part_number: int, manifest: dict | None = None
) -> str:
    """The background clip this part renders over.

    Os candidatos são a **união** do manifesto com o que já está publicado sob o
    prefixo: um segmento ainda não materializado concorre em pé de igualdade com
    um clipe subido à mão, e quem resolve a diferença é :func:`_ensure_background`,
    depois da escolha. Unir em vez de escolher uma das duas fontes é o que deixa
    as duas bibliotecas conviverem sem que uma esconda a outra.

    Falls back to the single ``background_video_key`` when nothing is published
    under the prefix: a bucket that was never filled still renders, instead of
    failing at the last step. The fixed key is the old behaviour, kept as a floor.
    """
    template = settings.CONFIG.template
    prefix = str(getattr(template, "background_prefix", "") or "")
    manifest = manifest if manifest is not None else await _load_background_manifest()

    if prefix:
        publicados = [k for k in await list_keys(settings.CONFIG.storage.bucket, prefix)
                      if is_clip(k)]
        keys = sorted(set(publicados) | set(manifest_keys(manifest)))
        if keys:
            return pick_background(keys, str(run_id), part_number, await _background_usage(session))
        log.warning("no background clips under prefix", prefix=prefix)
        # Degradação silenciosa de novo: o render funciona, mas todos os vídeos
        # passam a dividir o mesmo fundo fixo — que é o que a rotação existe
        # para evitar. Nenhum status registra isso.
        notify(
            "Nenhum clipe de fundo no bucket — usando o fundo fixo",
            level="warning",
            icon="⚠️",
            prefixo=prefix,
        )

    return template.background_video_key


async def _ensure_background(session, key: str, manifest: dict) -> str:
    """Garante que o clipe escolhido existe no bucket. Devolve a chave usável.

    Chave que não está no manifesto é clipe subido à mão: já está no bucket por
    definição e não há o que materializar.

    **Falha em materializar não derruba o run.** O download depende de rede e de
    um site de terceiro, e um soluço ali custaria um run que já pagou LLM, TTS e
    Whisper. Então a queda é para outro clipe já materializado, e só se não
    houver nenhum é que o fundo fixo entra. Esta é a sétima degradação silenciosa
    do pipeline: o vídeo sai, o run termina `scheduled`, e nada no status
    distingue o fundo sorteado do fundo de emergência — daí o aviso.
    """
    entrada = manifest_entry(manifest, key)
    if entrada is None:
        return key

    cfg = settings.CONFIG.backgrounds
    bucket = settings.CONFIG.storage.bucket
    prefix = str(getattr(settings.CONFIG.template, "background_prefix", "") or "")

    try:
        materializou = await ensure_available(
            key,
            entrada,
            bucket=bucket,
            segment_seconds=int(cfg.segment_seconds),
            video_filter=str(cfg.video_filter),
            quality=str(cfg.source_quality),
            crf=int(cfg.crf),
        )
    except Exception as exc:  # noqa: BLE001 — yt-dlp e ffmpeg levantam famílias largas
        publicados = [k for k in await list_keys(bucket, prefix) if is_clip(k) and k != key]
        alternativa = (
            pick_background(publicados, str(key), 0, await _background_usage(session))
            if publicados
            else settings.CONFIG.template.background_video_key
        )
        log.warning("background materialize failed", key=key, fallback=alternativa,
                    error=str(exc)[:200])
        notify(
            "Fundo não pôde ser baixado — vídeo sai com outro clipe",
            level="warning",
            icon="⚠️",
            pedido=key.rsplit("/", 1)[-1],
            usado=alternativa.rsplit("/", 1)[-1],
            erro=str(exc)[:160],
        )
        return alternativa

    if materializou:
        # Só depois de um download é que o cache pode ter passado do teto —
        # varrer o prefixo a cada render custaria uma listagem por vídeo sem
        # nada ter mudado.
        publicados = await list_keys(bucket, prefix)
        do_manifesto = [k for k in publicados if manifest_entry(manifest, k) is not None]
        await evict(session, bucket=bucket, materializados=do_manifesto, teto=int(cfg.cache_max))

    return key


async def _run_render(session, part: PipelinePart, run: PipelineRun) -> None:
    log.info("starting render", run_id=str(run.id), part=part.part_number)
    notify(
        "Render iniciado",
        level="debug",
        icon="🎞️",
        run=short_id(run.id),
        parte=f"{part.part_number}/{run.parts_count}",
    )
    part.status = PartStatus.render_pending
    await session.commit()

    # Gravado **antes** do render, não depois. É o que faz um re-render reusar a
    # mesma footage sem depender de recalcular, e é o que tira o clipe do bolso
    # dos disponíveis antes que a próxima parte escolha — o render leva minutos,
    # e nesse intervalo a escolha já tem de estar contabilizada.
    #
    # O manifesto é lido uma vez e passa pelas duas etapas: escolher entre os
    # candidatos e garantir que o escolhido existe. Reler custaria duas buscas no
    # bucket por parte, do mesmo objeto.
    manifest = await _load_background_manifest()
    if not part.background_key:
        part.background_key = await background_key_for(
            session, run.id, part.part_number, manifest
        )
        await session.commit()

    # Pode devolver outra chave: materialização que falha cai para um clipe já
    # disponível, e a coluna passa a registrar o que foi de fato usado — senão um
    # re-render insistiria para sempre na fonte que não baixa.
    background_key = await _ensure_background(session, part.background_key, manifest)
    if background_key != part.background_key:
        part.background_key = background_key
        await session.commit()

    blender = BlenderClient()
    video_id = await blender.create_video(
        video_file_key=background_key,
        music_key=settings.CONFIG.template.music_key,
        voice_key=part.audio_key,
        subtitle_key=part.srt_key,
        # A intro abre todas as partes, não só a primeira: é ela que dá a mesma
        # cara à série inteira. Qualquer uma das duas pode ser nula.
        card_key=run.card_key,
        hook_voice_key=run.hook_audio_key,
        hook_muted=_hook_is_muted(part, run),
    )

    job_id = await blender.create_job(video_id=video_id, template_id=_template_id_for(run))

    part.blender_job_id = job_id
    part.status = PartStatus.render_running
    await session.commit()

    result = await blender.poll_job(job_id)
    if result["status"] == "failed":
        raise RuntimeError(f"render job failed: {result.get('error')}")

    part.video_key = result["output_key"]
    part.status = PartStatus.render_done
    await session.commit()
    log.info(
        "render done",
        run_id=str(run.id),
        part=part.part_number,
        key=part.video_key,
        background=background_key,
    )
    notify(
        "Vídeo renderizado",
        icon="🎬",
        run=short_id(run.id),
        parte=f"{part.part_number}/{run.parts_count}",
        fundo=background_key.rsplit("/", 1)[-1],
    )


async def _schedule(session, run: PipelineRun) -> bool:
    """Hand every rendered part to the poster. ``False`` means the queue is full.

    Idempotent on purpose: parts that already carry a ``scheduled_at`` are
    skipped, so this can run again — after a restart, or once the queue drains —
    without double-posting what is already booked.

    As partes de uma história dividida saem **encadeadas**: cada uma leva o
    horário da anterior em ``follows_at``, e o poster a agenda um intervalo
    depois dele. Só a parte 1 disputa os horários preferidos do calendário —
    uma história partida é uma história continuada, não N posts soltos.
    """
    log.info("scheduling posts", run_id=str(run.id))
    notify("Agendando publicação", level="debug", icon="📋", run=short_id(run.id))
    run.status = PipelineStatus.scheduling
    await session.commit()

    tiktok = TikTokClient()
    parts = await _parts_of(session, run)
    #: Horário da última parte agendada. Atualizado também nas partes puladas
    #: por já terem `scheduled_at` — numa retomada, a parte 1 vem do banco e é
    #: dela que a parte 2 precisa pendurar.
    previous_slot: datetime | None = None

    for part in parts:
        if part.video_key is None:
            log.warning("part has no video_key, skipping schedule", part=part.part_number)
            notify(
                "Parte sem vídeo — não será publicada",
                level="warning",
                icon="⚠️",
                run=short_id(run.id),
                parte=part.part_number,
            )
            continue
        if part.scheduled_at is not None:
            previous_slot = part.scheduled_at
            continue

        try:
            data = await tiktok.schedule(
                video_key=part.video_key,
                classification=run.classification or {},
                part_number=part.part_number,
                series_id=str(run.id),
                total_parts=len(parts),
                follows_at=previous_slot,
                youtube_title=run.youtube_title,
            )
        except BufferQueueFull as exc:
            # Not a failure: the run keeps its rendered videos and stays in
            # ``scheduling``, which the scout reads as occupied capacity. That is
            # the backpressure link that was missing — ingestion now stops on its
            # own while the queue is full, instead of feeding it renders that die
            # at the very last step after everything has already been paid for.
            log.info(
                "buffer unavailable, run waiting",
                run_id=str(run.id),
                part=part.part_number,
                error=exc.error,
                pending=exc.pending_count,
                rejected_by_buffer=exc.rejected_by_buffer,
                retry_after=exc.retry_after,
            )
            # Cota estourada e fila cheia param o run do mesmo jeito, mas se
            # resolvem em lugares opostos: uma espera a janela virar, a outra
            # espera um post publicar. Um aviso só para as duas mandaria olhar
            # o lugar errado na metade das vezes.
            notify(
                "Cota da API do Buffer estourada — run esperando"
                if exc.is_rate_limit
                else "Fila do Buffer cheia — run esperando vaga",
                icon="⏸️",
                run=short_id(run.id),
                parte=part.part_number,
                na_fila=exc.pending_count,
                # Só quando o teto veio da recusa do Buffer: nesse caminho a
                # contagem local não vê o problema, e sem a mensagem o aviso
                # ficaria idêntico ao da fila cheia comum.
                recusa=exc.rejected_by_buffer,
                libera_em=_humanize_seconds(exc.retry_after),
            )
            return False

        raw_ts = data.get("scheduled_at")
        part.scheduled_at = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")) if raw_ts else None
        part.tiktok_video_id = data.get("buffer_update_id")
        part.youtube_video_id = data.get("youtube_update_id")
        await session.commit()

        notify(
            "Publicação agendada",
            icon="📅",
            run=short_id(run.id),
            parte=f"{part.part_number}/{len(parts)}",
            para=_when(part.scheduled_at),
            youtube="ok" if part.youtube_video_id else None,
        )

        # Sexta degradação silenciosa: o TikTok saiu, o run vai terminar
        # `scheduled` e nada no status distingue "publicado nos dois" de
        # "publicado só num". Sem este aviso, a ausência do vídeo no YouTube só
        # apareceria olhando a coluna `youtube_video_id` de um run específico.
        #
        # ⚠️ Condicionado a `youtube_enabled`: destino desligado não é
        # degradação, é configuração. Sem essa guarda, toda parte de todo run
        # dispararia um aviso enquanto o canal não estivesse conectado — e um
        # alarme que toca sempre é um alarme que ninguém lê.
        youtube_error = data.get("youtube_error")
        if data.get("youtube_enabled") and part.youtube_video_id is None:
            log.warning(
                "part not scheduled on youtube",
                run_id=str(run.id),
                part=part.part_number,
                error=youtube_error,
            )
            notify(
                "Parte não foi agendada no YouTube",
                level="warning",
                icon="⚠️",
                run=short_id(run.id),
                parte=part.part_number,
                motivo=youtube_error,
            )

        if part.scheduled_at is not None:
            previous_slot = part.scheduled_at

    run.status = PipelineStatus.scheduled
    await session.commit()
    log.info("pipeline scheduled", run_id=str(run.id))
    notify(
        "Run concluído",
        icon="🚀",
        run=short_id(run.id),
        partes=len(parts),
        primeira=_when(parts[0].scheduled_at) if parts else None,
    )
    # O dead-man's switch de produto. É pingado **só aqui**, no único ponto do
    # sistema que significa "saiu vídeo de verdade" — é o silêncio deste ping,
    # do lado de fora, que denuncia um pipeline que parou de produzir enquanto
    # todos os `/health` continuam respondendo 200.
    await ping("produced")
    return True


def _humanize_seconds(seconds: int | None) -> str | None:
    """``30837`` → ``"8h34"``. `None` some do aviso em vez de virar campo vazio.

    Segundos crus não dizem nada num alerta de celular: a diferença entre
    esperar meia hora e esperar meio dia é a diferença entre esperar e ir olhar.
    """
    if not seconds or seconds < 0:
        return None
    minutes, hours = (seconds // 60) % 60, seconds // 3600
    return f"{hours}h{minutes:02d}" if hours else f"{minutes}min"


def _when(moment: datetime | None) -> str | None:
    """Um horário legível num celular, no fuso da máquina.

    ``astimezone()`` sem argumento converte para o fuso local do processo — daí o
    ``TZ`` no compose. Sem ele o container roda em UTC e todo horário aparece 3h
    adiantado, o que é pior que não mostrar horário nenhum: parece informação
    correta.
    """
    if moment is None:
        return None
    return moment.astimezone().strftime("%d/%m %H:%M")


async def _parts_of(session, run: PipelineRun) -> list[PipelinePart]:
    result = await session.execute(
        select(PipelinePart).where(PipelinePart.run_id == run.id).order_by(PipelinePart.part_number)
    )
    return list(result.scalars().all())


async def recover_interrupted_runs() -> dict[str, int]:
    """Reconcile runs left mid-flight by a restart. Runs once, at startup.

    The pipeline executes inside a FastAPI background task, which dies with the
    process. Nothing used to notice: a run interrupted mid-render stayed
    ``processing`` forever, and because the scout counts that as occupied
    capacity, five interrupted runs stopped ingestion for good. Any active state
    at startup is by definition ownerless — no task survives a restart.

    Runs whose parts are all rendered only owe a scheduling call, so they are
    picked back up. The rest are failed with an explicit reason: re-running the
    pipeline from the top would duplicate parts and pay for the LLM and the
    speech a second time.
    """
    resumed = 0
    failed = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PipelineRun).where(PipelineRun.status.in_(ACTIVE_STATUSES))
        )
        runs = list(result.scalars().all())

        for run in runs:
            parts = await _parts_of(session, run)
            if parts and all(p.video_key is not None for p in parts):
                resumed += 1
                log.info("resuming interrupted run", run_id=str(run.id), status=run.status.value)
                asyncio.create_task(_schedule_in_background(run.id))
            else:
                failed += 1
                run.status = PipelineStatus.failed
                run.error = "interrompido por restart do orchestrator"
                log.warning("failing interrupted run", run_id=str(run.id))
                await session.commit()

    if runs:
        log.info("startup recovery done", resumed=resumed, failed=failed)
        notify(
            "Runs órfãos reconciliados no boot",
            level="warning",
            icon="🔧",
            retomados=resumed,
            perdidos=failed,
        )
    return {"resumed": resumed, "failed": failed}


async def retry_pending_schedules() -> int:
    """Re-offer runs parked on a full queue. Returns how many drained.

    This is what makes ``BufferQueueFull`` a pause rather than a loss: the run
    waits in ``scheduling`` until Buffer has room, and this pass — not a person —
    picks it back up.
    """
    drained = 0

    async with AsyncSessionLocal() as session:
        #: Só os ids, e cada run é buscado dentro do laço. O `rollback` do
        #: tratamento de erro expira **todos** os objetos da sessão, não só o
        #: que falhou: segurar os runs seguintes como instâncias ORM fazia a
        #: iteração seguinte tocar um objeto expirado, o SQLAlchemy tentar
        #: recarregá-lo de forma síncrona dentro do contexto async, e a
        #: varredura inteira morrer com `greenlet_spawn has not been called` —
        #: erro que não tem relação nenhuma com a falha original. Um run que
        #: falha não pode levar junto os que ainda nem foram tentados.
        result = await session.execute(
            select(PipelineRun.id).where(PipelineRun.status == PipelineStatus.scheduling)
        )
        for run_id in list(result.scalars().all()):
            run = await session.get(PipelineRun, run_id)
            if run is None:  # apagado entre a listagem e agora
                continue
            try:
                if await _schedule(session, run):
                    drained += 1
            except Exception as exc:  # noqa: BLE001 — one stuck run must not end the sweep
                log.error("retry schedule failed", run_id=str(run_id), error=str(exc))
                notify(
                    "Retomada de agendamento falhou",
                    level="error",
                    icon="❌",
                    run=short_id(run_id),
                    erro=str(exc)[:200],
                )
                await session.rollback()

    return drained


async def _schedule_in_background(run_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(PipelineRun, run_id)
        if run is None:
            return
        try:
            await _schedule(session, run)
        except Exception as exc:  # noqa: BLE001 — mirrors run_pipeline's own guard
            run.status = PipelineStatus.failed
            run.error = str(exc)
            log.error("resumed run failed", run_id=str(run_id), error=str(exc))
            notify(
                "Run retomado falhou",
                level="error",
                icon="❌",
                run=short_id(run_id),
                erro=str(exc)[:200],
            )
            await session.commit()


async def maintenance_loop() -> None:
    """Periodic drain of runs parked on a full Buffer queue.

    Outlives any single failure for the same reason the scout's loop does: a
    night with nobody watching is exactly when it must not stop.

    Carrega também o batimento do ``alive``. É o lugar certo por já ser o único
    laço que atravessa a noite: um ping em processo separado poderia continuar
    batendo com o orchestrador travado, que é exatamente a falha a detectar.
    """
    pipeline_cfg = getattr(settings.CONFIG, "pipeline", None)
    interval = int(getattr(pipeline_cfg, "retry_interval_seconds", 900) if pipeline_cfg else 900)
    log.info("maintenance_loop_started", interval_seconds=interval)

    while True:
        await asyncio.sleep(interval)
        # Antes do trabalho, não depois: o ping responde "este processo está de
        # pé", e uma varredura lenta ou travada não deve ser lida como morte.
        await ping("alive")
        try:
            drained = await retry_pending_schedules()
            if drained:
                log.info("maintenance drained runs", runs=drained)
                notify("Runs destravados da fila do Buffer", icon="▶️", runs=drained)
        except Exception as exc:  # noqa: BLE001 — the loop must survive the night
            log.error("maintenance_loop_failed", error=str(exc))
            notify(
                "Manutenção falhou",
                level="error",
                icon="❌",
                erro=str(exc)[:200],
            )
