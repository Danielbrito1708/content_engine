# CLAUDE.md — tiktok_poster

Serviço de agendamento e publicação de vídeos no TikTok via Buffer. Recebe `POST /schedule` do orchestrador com o vídeo pronto, calcula o próximo slot disponível, monta a caption e agenda no Buffer.

## Arquitetura

- `api/routes/schedule.py` — endpoint principal
- `api/routes/health.py` — `GET /health`, verifica conexão com Buffer
- `buffer/client.py` — `BufferClient`: `get_pending_posts()`, `create_post()`, `verify_connection()`
- `buffer/scheduler.py` — `next_available_slot()`: calcula próximo horário livre respeitando limite de fila
- `storage/client.py` — `generate_presigned_url()`: gera URL pré-assinada do MinIO/R2 para o Buffer baixar o vídeo
- `hashtags/selector.py` — `select_hashtags()` + `compose_caption()`
- `schemas/schedule.py` — `ScheduleRequest`, `ScheduleResponse`
- `hashtags.json` — pool configurável de hashtags + obrigatórias

## Features

### Endpoint de agendamento (`src/tiktok_poster/api/routes/schedule.py`)

`POST /schedule` — agenda um vídeo no Buffer para publicação no TikTok.

**Request** (`ScheduleRequest`):
- `video_key` (str) — MinIO/R2 key do vídeo renderizado
- `classification` (dict) — objeto de classificação do orchestrador (com `hashtag_hints`, `cta_per_part`, `parts`)
- `part_number` (int) — número da parte (1, 2, ...)
- `series_id` (str) — UUID do pipeline run (usado para log e rastreamento)

**Response** (`ScheduleResponse`):
- `scheduled_at` (datetime) — horário UTC agendado no Buffer
- `buffer_update_id` (str) — ID do post no Buffer

Erros: `429` com `{ error: "buffer_queue_full", pending_count }` se a fila do Buffer atingir o limite configurado.

### Scheduler de slots (`src/tiktok_poster/buffer/scheduler.py`)

`next_available_slot(pending_posts, posts_per_day, preferred_times, queue_limit) -> datetime | None`

- Escaneia dias à frente até encontrar um com menos de `posts_per_day` posts
- Horários preferidos configurados em `config.ini [posting] preferred_times` (padrão: `08:00,20:00` UTC)
- Retorna `None` se a fila já tem `queue_limit` posts (Buffer free: 10)

### Hashtags (`src/tiktok_poster/hashtags/selector.py`)

`select_hashtags(hints, mandatory, pool, max_total) -> list[str]`

- Prioridade: obrigatórias → hints do LLM → pool de fallback
- Deduplicação preservando ordem
- Total limitado por `config.ini [hashtags] max_total` (padrão: 8)
- Obrigatórias configuradas em `config.ini [hashtags] mandatory` (padrão: `#tiktokbrasil,#fyp`)
- Pool em `hashtags.json` (versionado no repo, editável sem deploy)

### Storage e URL pré-assinada (`src/tiktok_poster/storage/client.py`)

`generate_presigned_url(bucket, key, ttl_seconds) -> str`

- Gera URL GET pré-assinada via boto3 (compatível com MinIO e Cloudflare R2)
- TTL configurado em `config.ini [posting] presigned_url_ttl` (padrão: 21600s = 6h)
- **Requer storage publicamente acessível em produção** — em dev (MinIO local), o Buffer não consegue baixar o vídeo. Use R2 em produção.

## Configuração

Ver `.env.example`. Variáveis críticas:
- `BUFFER_ACCESS_TOKEN` — token da API do Buffer
- `BUFFER_PROFILE_ID` — ID do perfil TikTok no Buffer
- `MINIO_ENDPOINT` — em dev: `http://localhost:9000`; em prod: endpoint do R2

## Produção (Cloudflare R2)

```env
MINIO_ENDPOINT=https://ACCOUNT_ID.r2.cloudflarestorage.com
MINIO_ACCESS_KEY=R2_ACCESS_KEY_ID
MINIO_SECRET_KEY=R2_SECRET_ACCESS_KEY
```

O bucket deve ser o mesmo configurado nos demais serviços (`blender-jobs`).

## Testes

21 testes em 3 arquivos: `test_hashtags.py`, `test_scheduler.py`, `test_schedule.py`. Buffer e MinIO são sempre mockados.

```bash
poetry run pytest
```
