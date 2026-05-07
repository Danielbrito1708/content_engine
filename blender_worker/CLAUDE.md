# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

MVP in progress. `src/core/` (config, logger, bootstrap) is the infrastructure foundation. `src/blender_worker/` contains the worker implementation: FastAPI API, SQLAlchemy models, MinIO storage client, Blender render stub, and image compositor (comment card generator).

## Git rules

- Before any commit, run `git fetch origin && git status` to check if local is behind the remote. If it is, rebase first: `git pull --rebase origin main`.
- Convention or architecture changes (documentation rules, folder structure, naming) must be committed to the repo before being adopted locally — never diverge conventions between local and remote.

## Commands

```bash
# Install dependencies
poetry install

# Run the application (local dev)
python main.py

# Run tests
poetry run pytest

# Run a single test
poetry run pytest tests/path/to/test_file.py::test_function_name

# Docker (full stack: app + postgres + minio)
docker compose up

# Migrations (run from project root with DATABASE_URL set)
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1
```

The app requires a `.env` file at the project root. Copy from `.env.example` and set:
- `ROOT_DIR` — absolute path to the project root (required; `Settings.load()` raises if missing)
- `ENV` — `dev` or `prod` (defaults to `dev`; selects `config.ini` vs `config.prod.ini`)
- `DEBUG` — `true` or `false`
- `DATABASE_URL` — asyncpg connection string, e.g. `postgresql+asyncpg://postgres:postgres@localhost:5432/blender_worker`
- `MINIO_ENDPOINT` — MinIO URL, e.g. `http://localhost:9000`
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` — MinIO credentials
- `MINIO_BUCKET` — target bucket name

## Architecture

**Bootstrap flow**: importing `from src.core import *` (as `main.py` does) immediately triggers `bootstrap._init()` at module level. This calls `load_dotenv()`, then `Settings.load()`, then `log_setup()`, then installs the exception hook. The resulting `settings` instance is exposed via `src/core/__init__.py`. There is no lazy initialization.

**Config system** (`src/core/config.py`): `Settings.load()` reads the `.ini` file selected by `ENV`, then uses `_create_section_model()` to dynamically build a Pydantic model per section via `create_model`. Types are inferred from string values with `_infer_type()`. The result is a frozen `Settings` dataclass where `settings.CONFIG.<section>.<key>` gives typed access.

**Logger** (`src/core/logger.py`): reads `config.ini` directly via `ConfigParser` at module import time — before `Settings` is available. This is a known duplication (TODO in the code). In `dev`, structlog uses `ConsoleRenderer`; in `prod`, it uses `JSONRenderer`. The file handler uses `RotatingFileHandler` with a custom `RichTracebackFormatter` that renders Rich tracebacks to plain text for log files.

**Worker module** (`src/blender_worker/`):
- `api/app.py` — FastAPI app; first import is `from src.core import settings` to guarantee bootstrap runs before any other module reads env vars.
- `api/routes/health.py` — `GET /health`: pings the DB (SELECT 1) and MinIO (list_buckets). Returns `{"status": "ok"|"degraded", "checks": {...}}`.
- `api/routes/jobs.py` — `POST /jobs` (create + enqueue), `GET /jobs/{id}` (poll status).
- `api/routes/images.py` — `POST /images/render`: composes a comment card PNG and uploads to MinIO. Synchronous. See `## Features`.
- `db/models.py` — `Job` model with UUID PK, `JobStatus` enum (pending/running/completed/failed), `template_key`, `output_key`, `params` (JSONB), `error`, timestamps.
- `db/engine.py` — async SQLAlchemy engine + `AsyncSessionLocal` + `get_session()` dependency. Reads `DATABASE_URL` from env at import time (safe because `app.py` triggers bootstrap first).
- `schemas/job.py` — `JobCreate` (request) and `JobResponse` (response) Pydantic models.
- `schemas/image.py` — `ImageRenderRequest` and `ImageRenderResponse` Pydantic models.
- `storage/client.py` — `get_s3_client()` returns a boto3 S3 client pointed at MinIO via env vars. Also has `download_file` and `upload_file` async helpers.
- `worker.py` — `render_job(job_id)` async function: updates status to running, runs Blender CLI (stub with TODOs for download/render/upload), updates to completed/failed. Called via FastAPI `BackgroundTasks`.
- `image/text.py` — word wrap + text block height calculation. See `## Features`.
- `image/composer.py` — comment card compositor (rounded rect + assets + text → PNG bytes). Includes guide schema models. See `## Features`.

## Documentation rules

- **Every new feature must be documented in the `## Features` section of this file** before the work is considered done.
- Document: module path, public API (functions/classes/endpoints), inputs/outputs, and how it fits into the overall pipeline.
- Keep entries concise — enough for a future session to understand what exists without reading the source.

## Features

### Image text rendering (`src/blender_worker/image/text.py`)

Wraps text and computes block dimensions for the image compositor.

- `wrap_text(text, font, max_width) -> list[str]` — breaks `text` into lines that fit within `max_width` pixels. Respects explicit `\n`, never drops words that exceed `max_width` on their own.
- `measure(text, font, max_width, line_spacing=4) -> TextBlock` — calls `wrap_text` and returns a frozen `TextBlock(lines, line_height, line_spacing)`. `TextBlock.total_height` gives the full pixel height of the text block including inter-line spacing.
- Font must be a `PIL.ImageFont.FreeTypeFont` (load with `ImageFont.load_default(size=N)` for tests or `ImageFont.truetype(path, size)` for production).
- Tests: `tests/test_image_text.py` (10 tests, no DB/MinIO required).

### Image compositor (`src/blender_worker/image/composer.py`)

Composes a comment card image (rounded rect background + positioned assets + wrapped text) and returns PNG bytes. No DB or MinIO required at call time — callers are responsible for downloading assets and passing raw bytes.

**Schema (Pydantic models for the template guide JSON):** `CommentGuide`, `Canvas`, `Background`, `Padding`, `AssetSpec`, `Size`, `Position`, `TextSpec`.

**Public API:**
- `load_guide(path: Path) -> CommentGuide` — parses a guide JSON file from disk.
- `load_font(guide, root_dir) -> FreeTypeFont` — loads the TrueType font referenced by the guide.
- `compose(guide, text, asset_images, font, line_spacing=4) -> bytes` — renders and returns raw PNG bytes. `asset_images` is `dict[str, bytes]` keyed by asset `id`; missing ids are silently skipped. Canvas height grows automatically to fit text and assets.

**Canvas height rule:** `padding.top + max(tallest_asset_spec_height, text_block_height) + padding.bottom`.

**Template:** `templates/comment_default.json` — reference guide (800px wide, avatar slot + text). Uses `font_path: "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"` (installed via `fonts-dejavu-core` apt package in the Dockerfile — available in the container, but needs to be installed locally for dev outside Docker).

- Tests: `tests/test_image_composer.py` (9 tests, no DB/MinIO required).

### Image render endpoint (`src/blender_worker/api/routes/images.py`)

`POST /images/render` — composes a comment card and uploads the result to MinIO. Synchronous response (fast — Pillow, not Blender).

**Request** (`ImageRenderRequest`):
- `template` (str) — name of the guide file in `templates/` (e.g., `"comment_default"` → `templates/comment_default.json`). Returns 404 if file does not exist.
- `text` (str) — comment text; word-wrapped automatically.
- `assets` (dict, optional) — `{"id": "minio_key"}` overrides per-render; assets not listed fall back to the guide's default `minio_key`. Missing assets are silently skipped by the compositor.
- `output_key` (str, optional) — MinIO key for the output PNG. Auto-generated as `renders/<uuid>.png` if omitted.

**Response** (`ImageRenderResponse`): `{"output_key": "renders/abc123.png"}`.

**Schemas:** `src/blender_worker/schemas/image.py` (`ImageRenderRequest`, `ImageRenderResponse`).

- Tests: `tests/test_image_render.py` (5 tests; `load_font` and `get_s3_client` are monkeypatched — DB must be up for `clean_db` fixture, MinIO not required).

## Testing rules

- **Every new functionality must have tests.** No feature, route, model change, or worker behavior is complete without corresponding tests.
- Tests live in `tests/` and mirror the module being tested (e.g., `tests/test_jobs.py` for `api/routes/jobs.py`).
- Cover both the happy path and error/edge cases (404s, failures, missing data).
- Tests are integration tests — they run against the real DB and MinIO (docker compose must be up).
- Exception: pure computation modules (e.g., `image/text.py`) don't need docker compose — test them directly.
- Run with `poetry run pytest`. All tests must pass before any work is considered done.
- Event loop config: `asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope = "session"`, `asyncio_default_test_loop_scope = "session"` — do not change these; the async SQLAlchemy engine requires a single shared loop per session.

## Known tech debt

- `logger.py` reads `config.ini` directly instead of reusing the `Settings` system — causes duplicate config parsing on startup.
- `worker.py` uses FastAPI `BackgroundTasks` — jobs are lost if the container restarts mid-render. For production, replace with a proper queue (Celery + Redis, or similar).
- Blender version is installed from the Debian apt repo (3.x). If templates require Blender 4.x, the Dockerfile needs updating to fetch the binary directly from the official download.
- `render_job()` in `worker.py` has three TODOs: download template from MinIO, run Blender subprocess, upload rendered output.
- MinIO bucket is not auto-created on startup — if the bucket configured in `config.ini [storage] bucket` doesn't exist, `POST /images/render` returns 500. Create manually: `mc mb local/<bucket>` or via the MinIO console (localhost:9001).

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
