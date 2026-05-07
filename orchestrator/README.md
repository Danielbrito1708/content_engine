# orchestrator

Coordenador central do pipeline de geração de conteúdo. Recebe roteiros via API, orquestra `llm_service → tts_service → blender_worker → tiktok_poster` e persiste o estado de cada etapa.

---

## Setup local

**Pré-requisitos:** Python 3.11+, Poetry, PostgreSQL (banco `orchestrator`).

```bash
cd orchestrator
cp .env.example .env   # preencher com as variáveis abaixo
poetry install

# Aplicar migrations
alembic upgrade head

# Iniciar (porta 8000)
python main.py
```

Para a stack completa (todos os serviços + DB + MinIO/R2):

```bash
# na raiz do monorepo
docker compose up --build
```

---

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `ROOT_DIR` | Sim | Caminho absoluto para a raiz do serviço |
| `ENV` | Não | `dev` (padrão) ou `prod` |
| `DEBUG` | Não | `true` ou `false` |
| `DATABASE_URL` | Sim | `postgresql+asyncpg://user:pass@host:port/orchestrator` |
| `MINIO_ENDPOINT` | Sim | Endpoint do MinIO ou Cloudflare R2 |
| `MINIO_ACCESS_KEY` | Sim | Access key do MinIO/R2 |
| `MINIO_SECRET_KEY` | Sim | Secret key do MinIO/R2 |
| `MINIO_BUCKET` | Sim | Nome do bucket (ex: `blender-jobs`) |
| `BLENDER_TEMPLATE_ID` | Sim | UUID do template pré-registrado em `POST blender_worker/templates` |

URLs dos serviços dependentes ficam em `config.ini [services]`:

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

---

## Endpoints

### GET /health

Verifica conectividade com o banco de dados.

```bash
curl http://localhost:8000/health
```

```json
{ "status": "ok", "checks": { "db": "ok" } }
```

---

### POST /pipeline — Iniciar pipeline

```bash
curl -X POST http://localhost:8000/pipeline \
  -H "Content-Type: application/json" \
  -d '{"script": "Você sabia que a água quente congela mais rápido que a fria? Esse fenômeno é o efeito Mpemba.", "metadata": {}}'
```

```json
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

O pipeline roda em background. Use `GET /pipeline/{id}` para acompanhar o progresso.

---

### GET /pipeline/{id} — Consultar status

```bash
curl http://localhost:8000/pipeline/34319ef6-fb57-4c32-9e9e-924f074dfd92
```

```json
{
  "id": "34319ef6-...",
  "status": "scheduled",
  "parts_count": 1,
  "classification": {
    "content_type": "educativo",
    "tone": "shocking",
    "hashtag_hints": ["#ciencia", "#curiosidades"]
  },
  "error": null,
  "parts": [
    {
      "id": "...",
      "part_number": 1,
      "status": "render_done",
      "audio_key": "audio/.../part_1.mp3",
      "video_key": "outputs/....mp4",
      "scheduled_at": "2026-05-08T08:00:00Z"
    }
  ],
  "created_at": "...",
  "updated_at": "..."
}
```

**Ciclo de status do pipeline:**
```
pending → refining → refined → processing → scheduling → scheduled → posted
                                                                    ↘ failed
```

---

### GET /pipeline — Listar pipelines

```bash
# Últimos 5 runs
curl "http://localhost:8000/pipeline?limit=5&offset=0"

# Paginação
curl "http://localhost:8000/pipeline?limit=20&offset=20"
```

---

## Rodar testes

Os testes são de integração e requerem o banco `orchestrator` disponível. Serviços externos (llm, tts, blender, tiktok) são mockados com `respx`.

```bash
# Subir só o DB
docker compose up db -d

# Rodar testes
cd orchestrator
poetry run pytest

# Rodar teste específico
poetry run pytest tests/test_worker.py::test_run_pipeline_success
```

16 testes em 2 arquivos: `tests/test_pipeline.py` e `tests/test_worker.py`.
