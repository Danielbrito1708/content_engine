# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

MVP in progress. `src/core/` (config, logger, bootstrap) is the infrastructure foundation. `src/blender_worker/` contains the worker implementation: FastAPI API, SQLAlchemy models, MinIO storage client, Blender render pipeline, and image compositor (comment card generator).

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
- `worker.py` — `render_job(job_id)` async function: downloads all assets + template from MinIO into a tmpdir, runs Blender twice (assembly via `scripts/edit_video.py`, then render with `-a`), uploads the MP4 output, updates status to completed/failed. Called via FastAPI `BackgroundTasks`.
- `scripts/edit_video.py` — Python script that runs **inside** Blender's interpreter (`blender -b template.blend -P edit_video.py -- job_config.json`). Sets up the VSE: movie strip (ch1), music strip at volume 0.2 with fade-out keyframes (ch2), voice strip at volume 1.0 (ch3), word-level text subtitles from `.srt` (ch4). Saves `.blend` and sets render output to the MP4 path. `bpy` is imported inside `main()` only, and `main()` is behind an `if __name__ == "__main__"` guard, so the pure helpers are importable (and tested) outside Blender.
- `image/text.py` — word wrap + text block height calculation. See `## Features`.
- `image/composer.py` — comment card compositor (rounded rect + assets + text → PNG bytes). Includes guide schema models. See `## Features`.

## Documentation rules

- **Every new feature must be documented in the `## Features` section of this file** before the work is considered done.
- Document: module path, public API (functions/classes/endpoints), inputs/outputs, and how it fits into the overall pipeline.
- Keep entries concise — enough for a future session to understand what exists without reading the source.

## Features

### Blender render pipeline (`src/blender_worker/worker.py` + `scripts/edit_video.py`)

Assembles video assets in Blender VSE and renders to MP4. Triggered by `POST /jobs` → `BackgroundTasks`.

**`render_job(job_id)` flow:**
1. Sets job status → `running`
2. Creates a tmpdir and downloads all assets from MinIO: `template.blend`, `template.json`, video, music, voice, subtitles (`.srt`)
3. Writes `job_config.json` with paths and timing
4. Runs `blender -b template.blend -P scripts/edit_video.py -- job_config.json` → saves `output.blend`
5. Runs `blender -b output.blend -a` → renders `final.mp4`
6. Uploads `final.mp4` to MinIO as `outputs/{job_id}/final.mp4` and `output.blend` as `outputs/{job_id}/output.blend`
7. Sets `job.output_key` and `job.blend_key`; status → `completed` (or `failed` + error message on any exception)
8. Cleans up tmpdir

**Background asset validation** — `check_movie_strip(strip, path, min_frames=MIN_MOVIE_FRAMES)` runs on the ch1 movie strip right after it is added and raises `ValueError` if `frame_duration < 2`, failing the job with the offending path in the message.

A file with no decodable video track *still loads* as a movie strip — Blender hands back one placeholder frame instead of raising. Without this check the render succeeds and quietly emits a black background for the entire video: the job reports `completed`, the MP4 has a plausible size and duration, and nothing downstream can distinguish a broken asset from a deliberately dark one. Measured against the real RNA: the 1 KB `assets/background.mp4` placeholder reports `frame_duration=1`; a valid 5s/30fps clip reports `150`. Two frames is the floor that separates them — a genuinely 1-frame background is a still image and belongs in an image strip.

Pure (takes anything with a `frame_duration`), so it is tested without Blender.

**Render length** — `content_end_frame(strips, bed_channels, fallback)` sets `scene.frame_end`. The music **and the background video** are *beds*: each is however long its asset happens to be, so neither may define where the video ends — only the narration and its subtitles do. Measured: a 90s background under a 68s narration rendered 22s of dead air after the last word left the screen. Previously only music was excluded, which went unnoticed because the placeholder background was a single frame. A bed *shorter* than the narration is deliberately not handled: the tail goes black, which is a problem to fix in the asset. Pure, so the rule is tested without Blender.

**`scripts/edit_video.py` VSE layout:**
- Scene — `fps = frame_rate` **and `fps_base = 1.0`**. Blender's effective fps is `fps / fps_base`, and `fps_base` comes from the `.blend` (the current `template.blend` is `6/0.1` = 60fps). Leaving it alone makes the scene run at `frame_rate / 0.1` — 10x off, which desyncs every frame-based timing and stretches sound strips 10x.
- Ch1 — movie strip (video file), starts at `intro_start + 1`
- Ch2 — music strip, volume 0.2; if `music_fade_out` in timing: keyframed fade from 0.2 → 0.0 between `music_fade_out` and `frame_end`
- Ch3 — voice strip, volume 1.0, starts at `speech_start + 1`
- Ch4 — word-level text subtitles from `.srt` — see **Word-level subtitles** and **Subtitle typography** below

**`template.json` format:** see `docs/vision.md` — `frame_rate`, `frame_end`, `channels` (ch numbers), `timing` (frame offsets including optional `music_fade_out`), optional `subtitles` block.

**Blender binary:** configured in `config.ini [blender] bin` → `/usr/local/bin/blender` (symlink to Blender 4.2 LTS in Docker).

- Tests: `tests/test_worker.py` (3 tests; Blender not required — subprocess and I/O are fully mocked).

### Word-level subtitles (`scripts/edit_video.py`)

The `.srt` produced by `tts_service` has **one entry per word** (Whisper `word_timestamps=True`). One text strip is created per entry on the subtitles channel.

**Public API (pure, no `bpy` — importable outside Blender):**
- `parse_srt(path) -> list[(start_ts, end_ts, text)]`
- `ts_to_frame(timestamp, frame_rate) -> int` — rounds to the nearest frame
- `build_subtitle_timeline(entries, frame_rate, frame_offset=0, fade_frames=3, max_hold_seconds=0.4, rise_frames=4) -> list[dict]` — returns specs with `start`, `end`, `text`, `fade_in`, `fade_out`, `rise` (all in frames)

`import_subtitles(scene, vse, srt_path, channel, frame_rate, frame_offset=0, fade_frames=3, max_hold_seconds=0.4, rise_frames=4, rise_offset=0.025) -> int` consumes the specs and creates the strips; returns the strip count. It needs `scene` because strip keyframes live on the scene's action, not on the strip.

**Timeline rules** (why each exists is in root `docs/vision.md`):
- `frame_offset` — SRT timestamps are relative to the narration audio, so `main()` passes `speech_start + 1`. Without it subtitles run ahead by the length of the intro.
- Each word is held until the next one starts, capped at `max_hold_seconds` past its own end — so a word doesn't linger through a real pause.
- Strip ends are clamped to the next start: never overlapping (overlapping strips get auto-moved to another channel by Blender).
- Minimum duration 1 frame; words landing on the same frame are merged into one strip (never dropped).
- Fades apply only at the edge of a real gap and on the first/last strip — fading between adjacent words reads as flicker. `fade_frames` is capped at ⅓ of the strip so short words can't get inverted `blend_alpha` keyframes. `fade_frames: 0` disables fades.
- **Rise (entrance animation)** — every word starts `rise_offset` below `SUBTITLE_Y` and animates up to it over `rise` frames, `SINE`/`EASE_OUT`. Unlike the fade this applies to *every* word: it's what makes each word read as a distinct pop even between back-to-back words, without touching opacity. Capped at `duration - 1` so the word reaches rest before the strip ends. Interpolation is pinned explicitly (`_set_easing`) because new keyframes otherwise inherit the developer's Blender preferences.

**Template config** (optional block in `template.json`):
```json
"subtitles": { "fade_frames": 3, "max_hold_seconds": 0.4, "rise_frames": 4, "rise_offset": 0.025 }
```
`rise_offset` is a fraction of frame height (0.025 ≈ 48px at 1080×1920); `rise_frames: 0` disables the animation. Resting position is `SUBTITLE_Y = 0.05`.

- Tests: `tests/test_subtitles.py` (16 tests, marked `no_db` — no docker compose, no Blender needed).

### Subtitle typography (`scripts/edit_video.py`)

Typeface, fill colour and outline for the word-level text strips. Default: **Futura Bold, white with a black outline**.

**Public API (pure, no `bpy`):**
- `resolve_font_path(configured=None, candidates=DEFAULT_FONT_CANDIDATES, exists=os.path.exists) -> str | None` — first font file that exists. `exists` is injectable for tests.
- `resolve_subtitle_style(config=None, exists=os.path.exists) -> dict` — reads the `subtitles` block into `{font_path, font_size, color, use_outline, outline_color, outline_width}`. Colours accept `[r,g,b]` or `[r,g,b,a]`; wrong channel counts raise `ValueError`. `outline_width` is clamped to 0..1.

**bpy-side:**
- `load_subtitle_font(font_path) -> VectorFont | None` — `bpy.data.fonts.load(..., check_existing=True)`. Called **once** in `import_subtitles`, outside the strip loop — a video has hundreds of word strips and per-strip loading would duplicate the datablock.
- `apply_text_style(strip, style, font=None)` — assigns to the strip. `font=None` leaves `strip.font` alone; `font_size=None` leaves the size alone.

`import_subtitles(..., style=None)` takes the resolved style; `main()` passes `resolve_subtitle_style(subs)`.

**Font fallback chain** — `subtitles.font_path` → `assets/fonts/Futura-Bold.ttf` → `.otf` → `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`. A missing font degrades the look, it never fails the render.

**`Futura-Bold.ttf` is committed in `assets/fonts/`** — that path is inside the Docker build context (`build: ./blender_worker`), so `COPY . .` puts it in the image. A font at the **monorepo root would not reach the container** and the render would silently fall back to DejaVu. Filename is case-sensitive on Linux. Fonts and `.blend` files are marked `binary` in the root `.gitattributes` — the repo is developed on Windows with `core.autocrlf=true`, where a mis-detected binary gets newline-converted and breaks at render time.

**Requires Blender 4.2+** — `use_outline`/`outline_color`/`outline_width` do not exist before 4.2 (the Dockerfile pins 4.2.20). Verified against the real RNA, not assumed.

**Defaults and why:** `outline_width` is 0.24, not Blender's 0.05 — 0.05 is a hairline that vanishes over a bright frame, and past ~0.30 the outline merges between glyphs and closes the counters of round letters. `font_size` has no code default (the strip keeps Blender's 60); `template.json` sets 140, since 60 is too small for 1080×1920 — body size is a per-template design choice, not a pipeline invariant. The scene's view transform must stay `Standard` (as `template.blend` has it); under `AgX` white 1.0 renders at ~0.78.

- Tests: `tests/test_subtitles.py` (35 tests total, marked `no_db` — no docker compose, no Blender needed).

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
- Exception: pure computation modules (e.g., `image/text.py`, the subtitle timeline in `scripts/edit_video.py`) don't need docker compose — test them directly. Mark those files with `pytestmark = pytest.mark.no_db` so the autouse `clean_db` teardown skips its DB connection.
- Run with `poetry run pytest`. All tests must pass before any work is considered done.
- Event loop config: `asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope = "session"`, `asyncio_default_test_loop_scope = "session"` — do not change these; the async SQLAlchemy engine requires a single shared loop per session.

## Known tech debt

- `worker.py` uses FastAPI `BackgroundTasks` — jobs are lost if the container restarts mid-render. For production, replace with a proper queue (Celery + Redis, or similar).
- MinIO bucket is not auto-created on startup — if the bucket configured in `config.ini [storage] bucket` doesn't exist, `POST /images/render` returns 500. Create manually: `mc mb local/<bucket>` or via the MinIO console (localhost:9001).

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
