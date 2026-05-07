# Features — orchestrator

Descrição detalhada de cada funcionalidade: rotas HTTP, worker, clients, modelos de DB e utilitários.

---

## 1. Health Check

**Rota:** `GET /health`
**Arquivo:** `src/orchestrator/api/routes/health.py`

### Comportamento
- Executa `SELECT 1` no banco `orchestrator` via engine async
- Retorna `status: "ok"` se o DB responde, `status: "degraded"` caso contrário

### Resposta
```json
// 200 OK
{ "status": "ok", "checks": { "db": "ok" } }

// 200 OK — DB indisponível
{ "status": "degraded", "checks": { "db": "error: Connection refused" } }
```

> Sempre retorna HTTP 200 — o status de degradação está no corpo.

---

## 2. Pipeline API

**Rotas:** `POST /pipeline`, `GET /pipeline/{run_id}`, `GET /pipeline`
**Arquivo:** `src/orchestrator/api/routes/pipeline.py`

### POST /pipeline — Criar e iniciar pipeline

Cria um `PipelineRun` no banco e dispara `run_pipeline(run_id)` via `BackgroundTasks`. Retorna imediatamente com o estado inicial.

**Request** (`PipelineCreate`):
```json
{ "script": "Texto bruto do roteiro.", "metadata": {} }
```

| Campo | Obrigatório | Descrição |
|---|---|---|
| `script` | Sim | Roteiro em texto plano |
| `metadata` | Não | Dados extras passados ao LLM (ex: `{"source": "reddit"}`) |

**Resposta:**
```json
// 201 Created
{
  "id": "34319ef6-fb57-4c32-9e9e-924f074dfd92",
  "status": "pending",
  "parts_count": 1,
  "classification": null,
  "error": null,
  "parts": [],
  "created_at": "2026-05-07T04:27:17.745677Z",
  "updated_at": "2026-05-07T04:27:17.745677Z"
}
```

**Casos de erro:**
- `422` — `script` ausente ou tipo inválido

---

### GET /pipeline/{run_id} — Consultar status

**Resposta:**
```json
// 200 OK
{
  "id": "34319ef6-...",
  "status": "processing",
  "parts_count": 2,
  "classification": {
    "content_type": "educativo",
    "tone": "shocking",
    "hashtag_hints": ["#ciencia", "#curiosidades"]
  },
  "error": null,
  "parts": [
    { "id": "...", "part_number": 1, "status": "render_running", "audio_key": "audio/.../part_1.mp3", "video_key": null, ... },
    { "id": "...", "part_number": 2, "status": "pending", ... }
  ],
  "created_at": "...",
  "updated_at": "..."
}

// 404 Not Found
{ "detail": "Pipeline run not found" }
```

**Ciclo de status do `PipelineRun`:**
```
pending → refining → refined → processing → scheduling → scheduled → posted
                                                                    ↘ failed
```

**Ciclo de status de cada `PipelinePart`:**
```
pending → tts_running → tts_done → render_pending → render_running → render_done
                                                                    ↘ failed
```

---

### GET /pipeline — Listar pipelines

Retorna lista paginada de runs, ordenados por `created_at desc`.

**Query params:**
- `limit` (int, padrão 20)
- `offset` (int, padrão 0)

```bash
curl "http://localhost:8000/pipeline?limit=5&offset=0"
```

---

## 3. Worker — `run_pipeline`

**Função:** `run_pipeline(run_id: UUID)`
**Arquivo:** `src/orchestrator/worker.py`

Executado em background após `POST /pipeline`. Coordena as três fases do pipeline sequencialmente. Qualquer exceção não tratada define `status = failed` com a mensagem de erro.

### Fase 1 — `_refine`

1. Define `status = refining`, commit
2. Chama `LLMClient().refine(raw_script, metadata)` com timeout 120s
3. Armazena `refined_script`, `classification`, `parts_count` no run
4. Define `status = refined`, commit
5. Cria um `PipelinePart` por parte retornada pelo LLM

### Fase 2 — `_process_all_parts`

1. Define `status = processing`, commit
2. Busca todas as `PipelinePart` do run, ordenadas por `part_number`
3. Para cada parte em sequência: executa `_run_tts` → `_run_render`

**`_run_tts(part)`:**
1. Define `part.status = tts_running`, commit
2. Chama `TTSClient().generate(text, run_id, part_number)` → retorna `audio_key`
3. Salva `part.audio_key`, define `part.status = tts_done`, commit

**`_run_render(part)`:**
1. Define `part.status = render_pending`, commit
2. Gera SRT via `text_to_srt(part.script)` e faz upload para `subs/{run_id}/part_{n}.srt`
3. Chama `BlenderClient().create_video(background_video_key, music_key, voice_key, subtitle_key)` → `video_id`
4. Chama `BlenderClient().create_job(video_id, BLENDER_TEMPLATE_ID)` → `job_id`
5. Define `part.status = render_running`, salva `part.blender_job_id`, commit
6. Polling via `BlenderClient().poll_job(job_id)` — intervalo 10s, timeout 3600s
7. Salva `part.video_key = result["output_key"]`, define `part.status = render_done`, commit

### Fase 3 — `_schedule`

1. Define `status = scheduling`, commit
2. Para cada part com `video_key` definido: chama `TikTokClient().schedule(...)`
3. Salva `part.scheduled_at` e `part.tiktok_video_id` retornados
4. Define `status = scheduled`, commit

---

## 4. Clients

**Arquivo:** `src/orchestrator/clients/`
**URLs configuradas em:** `config.ini [services]`

### LLMClient (`clients/llm.py`)

```python
async def refine(script: str, metadata: dict) -> RefineResult
```

- `POST {llm_url}/refine` com timeout 120s
- Retorna `RefineResult(parts: list[str], classification: dict)`

### TTSClient (`clients/tts.py`)

```python
async def generate(text: str, run_id: str, part_number: int) -> str
```

- `POST {tts_url}/generate` com timeout 120s
- Retorna `audio_key` (string com key MinIO)

### BlenderClient (`clients/blender.py`)

```python
async def create_video(video_file_key, music_key, voice_key, subtitle_key) -> UUID
async def create_job(video_id: UUID, template_id: UUID) -> UUID
async def get_job_status(job_id: UUID) -> dict
async def poll_job(job_id: UUID, timeout=3600, interval=10) -> dict
```

- `create_video` → `POST {blender_url}/videos` (timeout 30s)
- `create_job` → `POST {blender_url}/jobs` (timeout 30s)
- `poll_job` → polling em `GET {blender_url}/jobs/{id}` a cada 10s; levanta `TimeoutError` após 3600s

### TikTokClient (`clients/tiktok.py`)

```python
async def schedule(video_key, classification, part_number, series_id) -> dict
```

- `POST {tiktok_url}/schedule` com timeout 30s
- Retorna `{"scheduled_at": "...", "buffer_update_id": "..."}`

---

## 5. Modelos de Banco de Dados

**Arquivo:** `src/orchestrator/db/models.py`
**Banco:** PostgreSQL — banco `orchestrator`

### PipelineRun

Tabela: `pipeline_runs`

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | UUID PK | Gerado automaticamente |
| `raw_script` | Text | Roteiro original enviado pelo usuário |
| `input_metadata` | JSON | Metadados extras opcionais |
| `refined_script` | Text | Roteiro pós-refinamento LLM (partes concatenadas) |
| `classification` | JSON | Objeto de classificação retornado pelo LLM |
| `parts_count` | Integer | Número de partes (1 para vídeo único) |
| `status` | Enum | `PipelineStatus` (ver ciclo acima) |
| `error` | Text | Mensagem de erro se `status = failed` |
| `created_at` | TimestampTZ | Criação automática |
| `updated_at` | TimestampTZ | Atualização automática via `onupdate` |

### PipelinePart

Tabela: `pipeline_parts`

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | UUID PK | Gerado automaticamente |
| `run_id` | UUID FK | Referência ao `PipelineRun` |
| `part_number` | Integer | Ordem da parte na série (começa em 1) |
| `script` | Text | Trecho do roteiro desta parte |
| `audio_key` | String(512) | Key MinIO do MP3 gerado pelo TTS |
| `video_key` | String(512) | Key MinIO do MP4 renderizado |
| `blender_job_id` | UUID | Job ID no blender_worker |
| `status` | Enum | `PartStatus` (ver ciclo acima) |
| `error` | Text | Mensagem de erro se falhou |
| `scheduled_at` | TimestampTZ | Horário agendado no Buffer |
| `posted_at` | TimestampTZ | Horário de postagem efetiva (preenchido futuramente) |
| `tiktok_video_id` | String(255) | ID do post no Buffer |

---

## 6. Utilitários

### Gerador de SRT (`src/orchestrator/utils/srt.py`)

```python
def text_to_srt(text: str, words_per_minute: int = 150) -> bytes
```

Converte texto plano em formato SRT com timing estimado. Divide em chunks de 8 palavras e calcula a duração de cada chunk a 150 palavras/minuto.

**Exemplo** (trecho com 3 chunks):
```
1
00:00:00,000 --> 00:00:03,200
Você sabia que a água quente

2
00:00:03,200 --> 00:00:06,400
congela mais rápido que a fria?

3
00:00:06,400 --> 00:00:09,600
Esse fenômeno é o efeito Mpemba.
```

Fórmula: `duration = len(chunk_words) * (60 / 150)` → ~0.4s por palavra.

### Storage (`src/orchestrator/storage/client.py`)

```python
async def upload_bytes(bucket: str, key: str, data: bytes, content_type: str) -> None
```

Upload não-bloqueante via `asyncio.to_thread` + boto3. Usado pelo worker para enviar o SRT ao MinIO/R2 antes de chamar o blender_worker.

---

## 7. Configuração

**Arquivo:** `config.ini`

```ini
[services]
llm_url = http://llm_service:8000
tts_url = http://tts_service:8000
blender_url = http://blender_worker:8000
tiktok_url = http://tiktok_poster:8000

[storage]
bucket = blender-jobs

[template]
background_video_key = assets/background.mp4
music_key = assets/music.mp3
```

**Variável de ambiente obrigatória além do `.env`:**

| Variável | Descrição |
|---|---|
| `BLENDER_TEMPLATE_ID` | UUID do template pré-registrado em `POST blender_worker/templates` |

---

## Resumo de Status

| Feature | Status |
|---|---|
| `GET /health` | ✅ Implementado |
| `POST /pipeline` | ✅ Implementado |
| `GET /pipeline/{id}` | ✅ Implementado |
| `GET /pipeline` | ✅ Implementado |
| Worker — fase refine | ✅ Implementado |
| Worker — fase TTS | ✅ Implementado |
| Worker — fase render | ✅ Implementado |
| Worker — fase schedule | ✅ Implementado |
| Modelos DB (`PipelineRun`, `PipelinePart`) | ✅ Implementado |
| Migração inicial | ✅ Aplicada |
| `text_to_srt` | ✅ Implementado |

## O que ainda falta implementar

- **`posted_at`**: o campo existe no modelo mas nunca é preenchido — o orchestrador não implementa polling pós-agendamento para confirmar publicação efetiva no TikTok.
- **Retry automático**: falhas em TTS ou render marcam `status = failed` sem retentar. O comportamento de retry está documentado como decisão em aberto em `docs/vision.md`.
- **`BackgroundTasks` em produção**: jobs são perdidos se o container reiniciar durante o processamento. Para produção, substituir por fila persistente (Celery + Redis ou similar).
- **`MutationError` do Buffer**: a resposta da API do Buffer pode conter um `MutationError` (ex: vídeo inacessível) — o `TikTokClient` propaga o retorno sem validar, e o worker marca como `scheduled` mesmo em caso de falha silenciosa.
