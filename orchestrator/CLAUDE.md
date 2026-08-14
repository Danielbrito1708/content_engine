# CLAUDE.md — orchestrator

Coordenador central do pipeline de geração de conteúdo. Recebe roteiros, orquestra llm_service → tts_service → blender_worker → tiktok_poster e persiste o estado de cada pipeline run.

## Arquitetura

**Bootstrap**: igual ao blender_worker — `from src.core import settings` em `app.py` dispara `bootstrap._init()` antes de qualquer outro import ler env vars.

**Módulos:**
- `api/routes/pipeline.py` — `POST /pipeline` (cria + inicia background), `GET /pipeline/{id}`, `GET /pipeline` (lista)
- `api/routes/health.py` — `GET /health`: ping no DB
- `db/models.py` — `PipelineRun` + `PipelinePart` + enums `PipelineStatus` / `PartStatus`
- `db/engine.py` — engine async + `AsyncSessionLocal` + `get_session()` dependency
- `schemas/pipeline.py` — `PipelineCreate`, `PipelineResponse`, `PartResponse`
- `clients/llm.py` — `LLMClient.refine(script, metadata) → RefineResult`
- `clients/tts.py` — `TTSClient.generate(text, run_id, part_number=1, rate=None, label=None, narrator_gender=None) → (audio_key, srt_key)`
- `clients/blender.py` — `BlenderClient`: `create_video(...)` (inclui `card_key`/`hook_voice_key`), `create_job(...)`, `render_card(text, template, output_key)`, `get_template_config(...)`, `get_job_status(...)`, `poll_job(...)`
- `clients/tiktok.py` — `TikTokClient.schedule(video_key, classification, part_number, series_id, total_parts=1, follows_at=None)` + exceção `BufferQueueFull`
- `clients/http.py` — `request(method, url, *, timeout, attempts)`: política única de retry
- `backgrounds.py` — `pick_background(keys, run_id, part_number)`, puro
- `hook_text.py` — `opens_with_hook(script, hook) -> bool`, puro: se a parte já abre pela frase gancho
- `storage/client.py` — `upload_bytes(...)` e `list_keys(bucket, prefix)` via boto3 (MinIO/R2)
- `worker.py` — `run_pipeline(run_id)` + `recover_interrupted_runs()`, `retry_pending_schedules()`, `maintenance_loop()`
- `api/app.py` — `lifespan`: reconcilia runs órfãos antes de servir, depois sobe o `maintenance_loop`

## Estado do PipelineRun

```
pending → refining → refined → processing → scheduling → scheduled → posted
                                                     ↘ failed (qualquer etapa)
```

Cada `PipelinePart` tem seu próprio `PartStatus`:
```
pending → tts_running → tts_done → render_pending → render_running → render_done
                                                                    ↘ failed
```

## Comandos

```bash
# Instalar dependências
poetry install

# Rodar local (requer .env e db up)
python main.py

# Testes (requer docker compose up com DB orchestrator disponível)
poetry run pytest

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Variáveis de ambiente

- `ROOT_DIR` — caminho absoluto para a raiz do serviço
- `ENV` — `dev` ou `prod`
- `DEBUG` — `true` ou `false`
- `DATABASE_URL` — asyncpg para o banco `orchestrator`
- `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` — credenciais MinIO/R2
- `BLENDER_TEMPLATE_ID` — UUID do template pré-registrado no blender_worker (`POST /templates`)

URLs dos serviços são configuradas em `config.ini [services]`.
Assets estáticos (background + música) em `config.ini [template]`.

## Features

### Pipeline API (`src/orchestrator/api/routes/pipeline.py`)

- `POST /pipeline` — cria `PipelineRun` e inicia `run_pipeline` em background. Retorna 201 com o estado inicial.
- `GET /pipeline/{id}` — retorna run + todas as parts com status atual.
- `GET /pipeline` — lista runs ordenados por `created_at desc`, com paginação (`limit`, `offset`).

**Request** (`PipelineCreate`): `script` (str), `metadata` (dict, opcional).
**Response** (`PipelineResponse`): `id`, `status`, `parts_count`, `classification`, `error`, `parts[]`, timestamps.

### Worker (`src/orchestrator/worker.py`)

`run_pipeline(run_id)` — executa as fases sequencialmente:

1. **`_refine`**: chama `LLMClient.refine()` → guarda `hook` e `narrator_gender` no run e cria `PipelinePart` para cada parte retornada
2. **`_narration_rate`**: lê `narration.rate` do template uma vez por run, antes do primeiro TTS
3. **`_run_hook_tts`**: narra a frase gancho num arquivo próprio (degradável — ver abaixo)
4. **`_render_card`**: compõe o card de comentário com o gancho (degradável — ver abaixo)
5. **`_process_all_parts`**: para cada part, executa `_run_tts` + `_run_render` sequencialmente
6. **`_schedule`**: chama `TikTokClient.schedule()` para cada part com `video_key` definido

**`_run_tts`**: chama `POST tts_service/generate` → salva `audio_key` e `srt_key` na part. Recebe o `rate` da narração e o repassa; `None` deixa o `tts_service` aplicar seu `TTS_RATE`. O roteiro vai inteiro: quem evita a repetição do gancho é o `hook_muted` do render, não um corte no texto.

### Voz do narrador (`narrator_gender`)

O `llm_service` devolve `narrator_gender` (`male` / `female` / `unknown`) — o gênero de quem conta a história. O orchestrador guarda em `PipelineRun.narrator_gender` (migration `005`, exposto em `PipelineResponse`) e manda em **toda** chamada ao `tts_service`: no gancho e em cada parte.

⚠️ **Requer a migration `005` aplicada** — sem a coluna, todo run morre no `_refine` com `UndefinedColumn`. O `CMD` do Dockerfile roda `alembic upgrade head` no boot, então subir com `--build` basta; fora do Docker é manual. Ver "Passos pendentes de deploy" no `CLAUDE.md` da raiz.

**Sai o gênero, nunca o nome da voz.** Qual voz corresponde a que gênero é decisão do `tts_service`, que conhece os providers; daqui sai um fato sobre o roteiro. Ausente, o campo é **omitido** do payload (não vai `null`), e a narração sai na voz padrão de lá.

**Do run, não da parte** — uma história dividida é a mesma pessoa contando. Mesma razão pela qual o gancho leva o mesmo `narrator_gender` **e** o mesmo `rate` das partes: basta a voz ou a velocidade divergir para o vídeo abrir com dois narradores.

`LLMClient.refine` lê `data.get("narrator_gender")` com fallback `"unknown"`, como faz com o `hook` — um `llm_service` antigo produz run sem gênero, não erro.

- Testes: `tests/test_narrator_voice.py` (10 — persistência no refino, fallback e normalização, o campo chegando ao TTS no gancho e nas partes, a omissão quando não há gênero, a garantia de que nenhum nome de voz sai daqui, o pipeline inteiro com um valor só, e o campo na API).

### Velocidade da narração (`_narration_rate`)

A velocidade da narração é definida no `template.json`, no bloco `narration.rate`. Como o TTS roda muito antes do render, o orchestrador precisa ler o template **antes** de chamar o `tts_service`:

1. `GET blender_worker/templates/{BLENDER_TEMPLATE_ID}/config` → `template.json` parseado
2. Extrai `narration.rate` (ex.: `"+30%"`)
3. Repassa como `rate` no `POST tts_service/generate`

Buscado **uma vez por run** em `run_pipeline`, antes do TTS do gancho — o template é o mesmo para todas as partes, e o gancho tem de sair no mesmo rate que elas.

**Degrada em silêncio.** Template sem bloco `narration`, blender_worker fora do ar, config inválido — tudo cai no `TTS_RATE` do `tts_service` com um warning no log, sem derrubar o run. Velocidade de narração é decisão estética; não vale falhar um pipeline por isso. Contrasta com o render, onde qualquer falha aborta.

O orchestrador **não valida o formato** do rate — quem valida é o `tts_service` (`422`). Duplicar a regex em dois serviços só criaria duas fontes de verdade.

**`_run_render`**:
1. Usa `part.srt_key` — legenda word-level já transcrita e subida pelo `tts_service` em `subs/{run_id}/part_{n}.srt`. O orchestrador não gera SRT.
2. `POST blender_worker/videos` com `background_video_key` + `music_key` (do config.ini) + `voice_key` (audio do TTS) + `subtitle_key` + `card_key`/`hook_voice_key` (a intro, nullable)
3. `POST blender_worker/jobs` com `video_id` + `BLENDER_TEMPLATE_ID`
4. Polling via `poll_job()` até `completed` ou `failed`
5. Salva `video_key = output_key` na part

**Pré-requisito de infra**: o template (`.blend` + `template.json`) e os assets estáticos (fundos, trilha) devem estar pré-registrados no blender_worker e no MinIO antes de rodar o pipeline.

⚠️ **`[template] music_key`** apontava para `assets/music.mp3`, que é **35s de silêncio digital** (medido: zero amostras não-nulas) — todo vídeo publicado até aqui saiu sem trilha, sem nenhuma falha. Agora aponta para `assets/music/lofi-goularte.mp3`. O render não valida conteúdo de áudio: um arquivo mudo continua sendo um render bem-sucedido. Ver `docs/vision.md` → "Trilha sonora".

### Áudio da frase gancho (`_run_hook_tts`)

O `llm_service` devolve `hook` — a frase de abertura do roteiro, isolada. O orchestrador guarda em `PipelineRun.hook` e narra essa frase sozinha, chamando o mesmo `POST tts_service/generate` com `label="hook"` → `hook_audio_key` (`audio/{run_id}/hook.mp3`) e `hook_srt_key`.

⚠️ **A etapa é degradável de propósito.** Falha no TTS do gancho vira `log.warning` e o run continua; falha no TTS de uma parte continua derrubando o run. A abertura é uma camada a mais sobre um vídeo que já se sustenta sem ela (a narração da parte 1 abre com essa mesma frase), e perder a camada não pode custar o vídeo. A ausência fica auditável em `hook_audio_key` nulo.

`LLMClient.refine` lê `data.get("hook")`: um `llm_service` antigo produz run sem gancho, não erro. Deploy dos dois serviços não é atômico.

Colunas em `pipeline_runs`: `hook`, `hook_audio_key`, `hook_srt_key` (migration `003`). Expostas em `PipelineResponse`.

### Abertura do vídeo: card + gancho (`_render_card`)

O card de comentário com a frase gancho é composto uma vez por run (`POST blender_worker/images/render` com `template` = `[template] card_template`, `text` = `run.hook`, `output_key` = `cards/{run_id}.png`) e guardado em `PipelineRun.card_key` (migration `004`, exposto em `PipelineResponse`).

`_run_render` manda `card_key` + `hook_audio_key` + `hook_muted` no `POST /videos`, para **todas as partes** — a intro é o que dá a mesma cara à série inteira, não uma abertura só da parte 1.

⚠️ **Degradável como o áudio do gancho**: falha na composição vira `warning`, `card_key` fica nulo e o vídeo sai sem abertura. Sem gancho, o card nem é pedido.

**`_run_hook_tts` agora recebe o `rate`.** Como o gancho é montado na frente da narração, ele tem de ser narrado na mesma velocidade — daí `_narration_rate()` ter subido para antes do primeiro TTS (era lido dentro de `_process_all_parts`, que agora recebe o valor pronto).

**O gancho é dito uma vez só** (`_hook_is_muted` + `hook_text.opens_with_hook`). Numa parte que já abre pela frase — a parte 1, por construção — tocar o `hook.mp3` na frente da narração diria a mesma coisa duas vezes. Então essa parte vai com `hook_muted=True`: o arquivo continua sendo mandado (é ele que mede quanto tempo o card fica na tela) e o blender_worker o monta mudo, deixando a narração dizer a frase.

A condição é o **texto** da parte, não o número dela: se o refino devolver o gancho abrindo a parte 2, ela se muta sozinha. Gancho reescrito pelo modelo dá `False`, e o vídeo volta a abrir com o áudio próprio. `part.script` nunca é reescrito e o TTS recebe o roteiro inteiro.

Regras completas em `docs/vision.md` → "Abertura do vídeo (intro: card + gancho)".

### Legendas

O orchestrador não participa da geração de legenda: o `tts_service` transcreve o próprio áudio (Whisper, timestamp por palavra) e devolve o `srt_key` junto com o `audio_key`. O orchestrador só persiste em `PipelinePart.srt_key` e repassa como `subtitle_key` ao `blender_worker`. Regras em `docs/vision.md` → "Legendas (word-level)".

### Rotação de background (`src/orchestrator/backgrounds.py`)

`pick_background(keys, run_id, part_number) -> str` — escolhe o clipe de fundo de cada parte entre os objetos sob `[template] background_prefix` (default `assets/backgrounds/`).

- **Determinístico** (`sha256(run_id:part)`, não `hash()`, que é salgado por processo): re-render devolve o mesmo fundo, e partes da mesma série caem em clipes diferentes.
- `background_key_for(run_id, part_number)` no worker lista o prefixo e cai no `background_video_key` único quando não há clipes.
- Levanta `ValueError` com lista vazia — quem chama decide o fallback.

### Resiliência (`src/orchestrator/worker.py`)

- **`BufferQueueFull` não é falha.** `_schedule` devolve `False`, o run fica em `scheduling` com os vídeos intactos, e o scout lê isso como capacidade ocupada (backpressure). `_schedule` é idempotente: parte com `scheduled_at` é pulada.
- **Séries saem encadeadas.** Cada parte manda `follows_at` = horário agendado da anterior, e o poster a coloca `series_gap_minutes` (30) depois. Só a parte 1 vai sem âncora e disputa os horários preferidos. Uma parte **pulada por já ter `scheduled_at` atualiza a âncora** antes do `continue` — senão um run retomado mandaria a parte 2 sem âncora e ela cairia no calendário, quebrando a série exatamente no caso em que o encadeamento importa. Parte sem `video_key` não vira âncora.
- **`total_parts` vem do run, não da classificação.** `_schedule` manda `len(parts)`. O poster lia `classification["parts"]`, chave que o `llm_service` nunca preencheu — o rótulo "(Parte 1/2)" nunca apareceu em post nenhum. Ver `tiktok_poster/CLAUDE.md`.
- **`recover_interrupted_runs()`** — roda no `lifespan` antes da primeira request. Estado ativo no boot é órfão por definição (as `BackgroundTasks` morrem com o processo): run com todas as partes renderizadas é retomado no agendamento, o resto vira `failed` com `"interrompido por restart"`.
- **`retry_pending_schedules()` / `maintenance_loop()`** — reoferece os runs parados a cada `[pipeline] retry_interval_seconds` (900s).
- **`clients/http.py`** — 3 tentativas com backoff exponencial em erro de transporte e 5xx. **4xx nunca é repetido**, incluindo o `429` do poster.

### Storage (`src/orchestrator/storage/client.py`)

`upload_bytes(bucket, key, data, content_type) -> None` — upload via boto3 (MinIO/R2). Usa `run_in_executor` para não bloquear o event loop.

`list_keys(bucket, prefix) -> list[str]` — lista paginada e **ordenada** (a rotação de background indexa nela, então a ordem tem que ser estável).

## Testing rules

Integration tests — requerem DB rodando. MinIO e serviços externos são mockados com `respx` e `monkeypatch`.

⚠️ **`clean_db` é autouse e apaga `pipeline_runs`/`pipeline_parts` sem filtro.** Apontar `DATABASE_URL` para o banco `orchestrator` do compose destrói o estado vivo. Rodar contra um descartável:

```bash
docker exec content_engine-db-1 psql -U postgres -c "CREATE DATABASE orchestrator_test;"
# no container, com DATABASE_URL=...@db:5432/orchestrator_test
alembic upgrade head && python -m pytest -q
```

98 testes em 11 arquivos: `test_pipeline.py` (6, API layer), `test_worker.py` (10, stages individuais + end-to-end), `test_series_scheduling.py` (6 — o encadeamento das partes: âncora, retomada, `total_parts`), `test_narrator_voice.py` (10 — o gênero do narrador do refino até o `tts_service`), `test_narration_rate.py` (13 — leitura do `narration.rate` do template, os fallbacks silenciosos e o repasse do `rate` ao `tts_service`), `test_hook_audio.py` (7 — gancho: persistência, key própria, skip sem gancho e falha degradável), `test_card_intro.py` (13 — card composto com o gancho, rate do gancho, o mute na parte que abre com ele e as keys chegando ao render), `test_hook_text.py` (12 — o predicado puro: prefixo, espaçamento, acentuação, e os casos em que não é abertura), `test_resilience.py` (10), `test_backgrounds.py` (6) e `test_http.py` (5).
