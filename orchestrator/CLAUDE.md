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
- `clients/tts.py` — `TTSClient.generate(text, run_id, part_number, rate=None) → (audio_key, srt_key)`
- `clients/blender.py` — `BlenderClient`: `create_video(...)`, `create_job(...)`, `get_template_config(...)`, `get_job_status(...)`, `poll_job(...)`
- `clients/tiktok.py` — `TikTokClient.schedule(...)` + exceção `BufferQueueFull`
- `clients/http.py` — `request(method, url, *, timeout, attempts)`: política única de retry
- `backgrounds.py` — `pick_background(keys, run_id, part_number)`, puro
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

`run_pipeline(run_id)` — executa as 3 fases sequencialmente:

1. **`_refine`**: chama `LLMClient.refine()` → cria `PipelinePart` para cada parte retornada
2. **`_process_all_parts`**: para cada part, executa `_run_tts` + `_run_render` sequencialmente
3. **`_schedule`**: chama `TikTokClient.schedule()` para cada part com `video_key` definido

**`_run_tts`**: chama `POST tts_service/generate` → salva `audio_key` e `srt_key` na part. Recebe o `rate` da narração e o repassa; `None` deixa o `tts_service` aplicar seu `TTS_RATE`.

### Velocidade da narração (`_narration_rate`)

A velocidade da narração é definida no `template.json`, no bloco `narration.rate`. Como o TTS roda muito antes do render, o orchestrador precisa ler o template **antes** de chamar o `tts_service`:

1. `GET blender_worker/templates/{BLENDER_TEMPLATE_ID}/config` → `template.json` parseado
2. Extrai `narration.rate` (ex.: `"+15%"`)
3. Repassa como `rate` no `POST tts_service/generate`

Buscado **uma vez por run** em `_process_all_parts`, não por part — o template é o mesmo para todas as partes.

**Degrada em silêncio.** Template sem bloco `narration`, blender_worker fora do ar, config inválido — tudo cai no `TTS_RATE` do `tts_service` com um warning no log, sem derrubar o run. Velocidade de narração é decisão estética; não vale falhar um pipeline por isso. Contrasta com o render, onde qualquer falha aborta.

O orchestrador **não valida o formato** do rate — quem valida é o `tts_service` (`422`). Duplicar a regex em dois serviços só criaria duas fontes de verdade.

**`_run_render`**:
1. Usa `part.srt_key` — legenda word-level já transcrita e subida pelo `tts_service` em `subs/{run_id}/part_{n}.srt`. O orchestrador não gera SRT.
2. `POST blender_worker/videos` com `background_video_key` + `music_key` (do config.ini) + `voice_key` (audio do TTS) + `subtitle_key`
3. `POST blender_worker/jobs` com `video_id` + `BLENDER_TEMPLATE_ID`
4. Polling via `poll_job()` até `completed` ou `failed`
5. Salva `video_key = output_key` na part

**Pré-requisito de infra**: o template (`.blend` + `template.json`) e os assets estáticos (background.mp4, music.mp3) devem estar pré-registrados no blender_worker e no MinIO antes de rodar o pipeline.

### Legendas

O orchestrador não participa da geração de legenda: o `tts_service` transcreve o próprio áudio (Whisper, timestamp por palavra) e devolve o `srt_key` junto com o `audio_key`. O orchestrador só persiste em `PipelinePart.srt_key` e repassa como `subtitle_key` ao `blender_worker`. Regras em `docs/vision.md` → "Legendas (word-level)".

### Rotação de background (`src/orchestrator/backgrounds.py`)

`pick_background(keys, run_id, part_number) -> str` — escolhe o clipe de fundo de cada parte entre os objetos sob `[template] background_prefix` (default `assets/backgrounds/`).

- **Determinístico** (`sha256(run_id:part)`, não `hash()`, que é salgado por processo): re-render devolve o mesmo fundo, e partes da mesma série caem em clipes diferentes.
- `background_key_for(run_id, part_number)` no worker lista o prefixo e cai no `background_video_key` único quando não há clipes.
- Levanta `ValueError` com lista vazia — quem chama decide o fallback.

### Resiliência (`src/orchestrator/worker.py`)

- **`BufferQueueFull` não é falha.** `_schedule` devolve `False`, o run fica em `scheduling` com os vídeos intactos, e o scout lê isso como capacidade ocupada (backpressure). `_schedule` é idempotente: parte com `scheduled_at` é pulada.
- **`recover_interrupted_runs()`** — roda no `lifespan` antes da primeira request. Estado ativo no boot é órfão por definição (as `BackgroundTasks` morrem com o processo): run com todas as partes renderizadas é retomado no agendamento, o resto vira `failed` com `"interrompido por restart"`.
- **`retry_pending_schedules()` / `maintenance_loop()`** — reoferece os runs parados a cada `[pipeline] retry_interval_seconds` (900s).
- **`clients/http.py`** — 3 tentativas com backoff exponencial em erro de transporte e 5xx. **4xx nunca é repetido**, incluindo o `429` do poster.

### Storage (`src/orchestrator/storage/client.py`)

`upload_bytes(bucket, key, data, content_type) -> None` — upload via boto3 (MinIO/R2). Usa `run_in_executor` para não bloquear o event loop.

`list_keys(bucket, prefix) -> list[str]` — lista paginada e **ordenada** (a rotação de background indexa nela, então a ordem tem que ser estável).

## Testing rules

Integration tests — requerem DB `orchestrator` rodando. MinIO e serviços externos são mockados com `respx` e `monkeypatch`.

29 testes em 3 arquivos: `test_pipeline.py` (API layer), `test_worker.py` (stages individuais + end-to-end) e `test_narration_rate.py` (13 testes: leitura do `narration.rate` do template, os fallbacks silenciosos e o repasse do `rate` ao `tts_service`).
