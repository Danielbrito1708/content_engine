# content_engine

TikTok content automation pipeline. Takes a plain-text script, refines it with an LLM, generates audio via TTS, assembles and renders the video in Blender, and schedules the post on TikTok.

```
POST /pipeline  →  llm_service  →  tts_service  →  blender_worker  →  tiktok_poster
```

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/install/) (v2)
- Git

No other local dependencies — everything runs in containers, including Blender.

## Setup

**1. Clone the repo**

```bash
git clone <repo-url>
cd content_engine
```

**2. Create your `.env`**

```bash
cp .env.example .env
```

Open `.env` and fill in the required values:

| Variable | Required | Description |
|---|---|---|
| `LLM_API_KEY` | Yes | API key for the LLM provider (OpenRouter / Anthropic / Chutes AI) |
| `LLM_MODEL` | No | Model to use (e.g. `anthropic/claude-3-haiku`). Has a default. |
| `TIKTOK_ACCESS_TOKEN` | Yes (to post) | TikTok API access token |
| `MINIO_ACCESS_KEY` | No | Defaults to `minioadmin` |
| `MINIO_SECRET_KEY` | No | Defaults to `minioadmin` |

Everything else in `.env.example` is pre-configured for the Docker environment and does not need to change.

**3. Start the stack**

```bash
docker compose up --build
```

First build takes a few minutes (Blender image is large). On subsequent runs, `--build` is only needed if you changed a `Dockerfile` or `pyproject.toml`.

Wait until all services print a startup message. The database and MinIO run health checks before the app containers start.

**4. Create the MinIO storage bucket**

Open the MinIO console at [http://localhost:9001](http://localhost:9001) and log in with `minioadmin / minioadmin`.

Create a bucket named **`blender-jobs`** (Buckets → Create Bucket).

> This only needs to be done once. The bucket persists in the `minio_data` Docker volume.

## Running the pipeline

Send a script to the orchestrator:

```bash
curl -X POST http://localhost:8000/pipeline \
  -H "Content-Type: application/json" \
  -d '{"script": "Your TikTok script text here.", "metadata": {}}'
```

The response returns a `pipeline_run_id`. Poll for status:

```bash
curl http://localhost:8000/pipeline/<pipeline_run_id>
```

The pipeline moves through these stages:

```
pending → refining → refined → tts_running → tts_done → render_running → render_done → scheduled → posted
```

If anything fails, status becomes `failed` with an error message.

The final rendered video is stored in MinIO under `outputs/<job_id>.mp4`. You can browse it at [http://localhost:9001](http://localhost:9001).

## Service ports

| Service | URL |
|---|---|
| Orchestrator (main API) | http://localhost:8000 |
| Blender worker | http://localhost:8001 |
| LLM service | http://localhost:8002 |
| TTS service | http://localhost:8003 |
| TikTok poster | http://localhost:8004 |
| MinIO API | http://localhost:9000 |
| MinIO Console | http://localhost:9001 |
| PostgreSQL | localhost:5433 |

Each service exposes a health endpoint at `GET /health`.

## Useful commands

```bash
# View logs for a specific service
docker compose logs -f orchestrator

# Restart a single service after a code change
docker compose up --build orchestrator

# Stop everything and remove containers
docker compose down

# Stop and also delete volumes (wipes DB and MinIO data)
docker compose down -v
```
