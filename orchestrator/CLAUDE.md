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
- `clients/tts.py` — `TTSClient.generate(text, run_id, part_number) → (audio_key, srt_key)`
- `clients/blender.py` — `BlenderClient`: `create_video(...)`, `create_job(...)`, `get_job_status(...)`, `poll_job(...)`
- `clients/tiktok.py` — `TikTokClient.schedule(video_key, classification, part_number, series_id)`
- `storage/client.py` — `upload_bytes(bucket, key, data, content_type)` via boto3 (MinIO/R2)
- `utils/srt.py` — ⚠️ código morto: `text_to_srt(text, words_per_minute) → bytes` gerava SRT com timing estimado. A legenda agora vem pronta do `tts_service` (word-level, transcrita do áudio). Pode ser removido.
- `worker.py` — `run_pipeline(run_id)`: executa o pipeline completo em background via FastAPI BackgroundTasks

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

**`_run_tts`**: chama `POST tts_service/generate` → salva `audio_key` e `srt_key` na part.

**`_run_render`**:
1. Usa `part.srt_key` — legenda word-level já transcrita e subida pelo `tts_service` em `subs/{run_id}/part_{n}.srt`. O orchestrador não gera SRT.
2. `POST blender_worker/videos` com `background_video_key` + `music_key` (do config.ini) + `voice_key` (audio do TTS) + `subtitle_key`
3. `POST blender_worker/jobs` com `video_id` + `BLENDER_TEMPLATE_ID`
4. Polling via `poll_job()` até `completed` ou `failed`
5. Salva `video_key = output_key` na part

**Pré-requisito de infra**: o template (`.blend` + `template.json`) e os assets estáticos (background.mp4, music.mp3) devem estar pré-registrados no blender_worker e no MinIO antes de rodar o pipeline.

### Legendas

O orchestrador não participa da geração de legenda: o `tts_service` transcreve o próprio áudio (Whisper, timestamp por palavra) e devolve o `srt_key` junto com o `audio_key`. O orchestrador só persiste em `PipelinePart.srt_key` e repassa como `subtitle_key` ao `blender_worker`. Regras em `docs/vision.md` → "Legendas (word-level)".

### Storage (`src/orchestrator/storage/client.py`)

`upload_bytes(bucket, key, data, content_type) -> None` — upload via boto3 (MinIO/R2). Usa `run_in_executor` para não bloquear o event loop.

## Testing rules

Integration tests — requerem DB `orchestrator` rodando. MinIO e serviços externos são mockados com `respx` e `monkeypatch`.

16 testes em 2 arquivos: `test_pipeline.py` (API layer) e `test_worker.py` (stages individuais + end-to-end).
