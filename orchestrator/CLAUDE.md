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
- `clients/tts.py` — `TTSClient.generate(text, run_id, part_number) → audio_key`
- `clients/blender.py` — `BlenderClient.create_job(...)`, `get_job_status(...)`
- `clients/tiktok.py` — `TikTokClient.schedule(video_key, classification, part_number, series_id)`
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

## Decisão em aberto (TODO no worker.py)

A integração com o `blender_worker` requer que o orchestrador registre os assets (vídeo de fundo, áudio gerado pelo TTS) como um `Video` no banco do blender_worker antes de criar o job. Isso será definido quando a API do blender_worker for estendida para aceitar asset keys diretamente.

## Comandos

```bash
# Instalar dependências
poetry install

# Rodar local (requer .env e db up)
python main.py

# Testes (requer db up com DATABASE_URL apontando para o DB orchestrator)
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
- `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` — credenciais MinIO

URLs dos serviços são configuradas em `config.ini [services]`.

## Testing rules

Mesmas regras do blender_worker: integration tests com DB real, `mock_run_pipeline` fixture para evitar background tasks nos testes de rota.

## Features

### Pipeline API (`src/orchestrator/api/routes/pipeline.py`)

- `POST /pipeline` — cria `PipelineRun` e inicia `run_pipeline` em background. Retorna 201 com o estado inicial.
- `GET /pipeline/{id}` — retorna run + todas as parts com status atual.
- `GET /pipeline` — lista runs ordenados por `created_at desc`, com paginação (`limit`, `offset`).
