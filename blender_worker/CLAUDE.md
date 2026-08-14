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
- `api/routes/templates.py` — `POST /templates`, `GET /templates/{id}`, `GET /templates/{id}/config`. See `## Features`.
- `storage/client.py` — `get_s3_client()` returns a boto3 S3 client pointed at MinIO via env vars. Also has `download_file`, `download_bytes` and `upload_file` async helpers.
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

**Background coverage** — `background_repeats(clip_frames, first_start, needed_end, max_repeats=MAX_BACKGROUND_REPEATS)` returns the start frames for the extra copies needed to cover the narration; `extend_background(vse, path, channel, strip, needed_end)` lays them down. Called in `main()` **after** `scene.frame_end` is decided.

A bed shorter than the narration used to render a **black tail** — no error, no warning: measured, a 45s clip under a 71s narration gave 26s of black with subtitles still popping over it. That was filed as an asset problem while every render shared one long hand-picked file; with backgrounds now drawn from a clip library (`orchestrator` rotates over `assets/backgrounds/`), a short clip is the normal case. Copies are laid exactly end to end — an overlap makes Blender relocate the strip to another channel, a gap is a black frame. `MAX_BACKGROUND_REPEATS` (60) bounds the degenerate case; past it the tail goes black as before. Pure, tested without Blender.

`background_repeats` **coerces its frame numbers to int**: the caller reads them off a strip, where Blender's RNA returns `frame_start`/`frame_duration` as floats, and `sequences.new_movie()` only takes ints — the repeats died with a `TypeError` mid-assembly. It never fired in production because a clip longer than the narration asks for no repeats at all, so the float never reached the API; it fired on the first validation render with a short clip.

**Render length** — `content_end_frame(strips, bed_channels, fallback)` **mais `end_padding_frames(...)`** sets `scene.frame_end` (see **The last word needs room to finish** below). The music **and the background video** are *beds*: each is however long its asset happens to be, so neither may define where the video ends — only the narration and its subtitles do. Measured: a 90s background under a 68s narration rendered 22s of dead air after the last word left the screen. Previously only music was excluded, which went unnoticed because the placeholder background was a single frame. A bed *shorter* than the narration is covered by repeating it (see **Background coverage** above) rather than by shrinking the timeline, which would cut narration mid-sentence. Pure, so the rule is tested without Blender.

**`scripts/edit_video.py` VSE layout:**
- Scene — `fps = frame_rate` **and `fps_base = 1.0`**. Blender's effective fps is `fps / fps_base`, and `fps_base` comes from the `.blend` (the current `template.blend` is `6/0.1` = 60fps). Leaving it alone makes the scene run at `frame_rate / 0.1` — 10x off, which desyncs every frame-based timing and stretches sound strips 10x.
- Ch1 — movie strip (video file), starts at `intro_start + 1`
- Ch2 — music strip, volume 0.2, with a keyframed fade to 0.0 over the last `music.fade_out_seconds` (see **No outro** below)
- Ch3 — voice strip, volume 1.0, starts at `intro_frames(...)` — `speech_start + 1` without a hook, after the hook when it is played, with the video when it is muted (see **Video intro** below)
- Ch4 — word-level text subtitles from `.srt` — see **Word-level subtitles** and **Subtitle typography** below
- Ch5 — hook narration (optional), volume 1.0, starts at `intro_start + 1`
- Ch6 — comment card image (optional), covering the intro; muted or not, its length comes from the hook file

**`template.json` format:** see `docs/vision.md` — `frame_rate`, `frame_end` (fallback only), `channels` (ch numbers), `timing` (frame offsets), optional `subtitles`, `card` and `music` blocks.

**Blender binary:** configured in `config.ini [blender] bin` → `/usr/local/bin/blender` (symlink to Blender 4.2 LTS in Docker).

- Tests: `tests/test_worker.py` (3 tests; Blender not required — subprocess and I/O are fully mocked).

### No outro — the video ends on the last narrated word (`scripts/edit_video.py`)

There is no closing segment: `scene.frame_end` is the end of the narration (see **Render length**) and the only thing marking the ending is the music bed fading under the last sentence. `timing.outro_start` / `timing.outro_end` were never read by any code and are gone from the shipped template.

**Public API (pure, no `bpy`):**
- `music_fade_frames(config, frame_rate, default_seconds=DEFAULT_MUSIC_FADE_SECONDS) -> int` — the `music` block's `fade_out_seconds` in frames. Seconds, not frames, because a fade is a musical length: the same number must mean the same ending at any `frame_rate`. `0` disables the fade.
- `music_fade_start(last_frame, fade_frames, first_frame=1) -> int | None` — `last_frame - fade_frames`, clamped to `first_frame`; `None` when there is nothing to fade.

**Counted back from the end, not from a frame in the template.** `timing.music_fade_out` was frame 840 against a `frame_end` of 900 — a 2s fade for the 30s draft it was written for. Now that `frame_end` comes from the narration, that number means something else: on a video *shorter* than 840 frames the keyframes go in out of order (1, 840, 701), Blender sorts them by frame, and the curve becomes `0.2 → 0.0 → 0.2` — the whole video in decline. Measured on a 701-frame timeline with the voice muted: the bed went from -26.1 dBFS at 0s to -73.4 dBFS at the end, versus a flat -26.0 until 21.5s and -49.0 in the last window with the fade counted from the end. `music_fade_out` is no longer read.

**Template config** (optional `music` block): `"music": { "fade_out_seconds": 1.5 }`. Defaults in code (`DEFAULT_MUSIC_FADE_SECONDS`) for the same reason the intro channels do — `template.json` lives in the bucket, and a template published before this feature has no `music` block.

- Tests: `tests/test_music.py` (12 tests, marked `no_db` — the pure arithmetic, the keyframes via a fake strip, and two guards on the shipped `template.json`).

### The last word needs room to finish (`scripts/edit_video.py`)

`scene.frame_end` is `content_end_frame(...) + end_padding_frames(...)`: the narration still decides where the video ends, but not on the very frame it stops.

**Public API (pure, no `bpy`):**
- `end_padding_frames(config, frame_rate, default_seconds=DEFAULT_END_PADDING_SECONDS) -> int` — the `narration` block's `tail_seconds` in frames. Seconds and not frames for the same reason as the music fade: it is a length of listening, and must mean the same pause at any `frame_rate`. `0` restores the old flush ending; negative values are floored at 0, so a template cannot end the render *before* the narration and cut a whole word.

**Why it exists.** Flush against the voice strip, the closing consonant is clipped and the video reads as ending mid-word. Two things compound: the strip end is already rounded to the frame, and the encoder's last audio packet lands right on the cut. `DEFAULT_END_PADDING_SECONDS` is 0.5 — a breath, long enough for the word to land and for the music fade to complete, short enough not to read as dead air.

**Order matters in `main()`.** The padding is added *before* `extend_background` and `music_fade_start` are called, so the background bed covers the extra frames (otherwise the tail is black) and the fade still reaches 0.0 exactly on the last frame.

**Template config** (the existing `narration` block, which the orchestrator already reads for `rate`):
```json
"narration": { "rate": "+30%", "tail_seconds": 0.5 }
```
Defaulted in code for the usual reason — `template.json` lives in the bucket, and the deployed one has no `tail_seconds` yet.

- Tests: `tests/test_subtitles.py` (6 tests next to `content_end_frame`'s, including a guard on the shipped `template.json`).

### Video intro — comment card + hook narration (`scripts/edit_video.py`)

The video opens with the comment card on screen while the hook phrase is read aloud. Both assets are optional and independent — `Video.card_key` / `Video.hook_voice_key` (migration `d4e5f6a7b8c9`), fed by the orchestrator, `None` meaning "no intro".

**Two modes, driven by `Video.hook_muted`** (migration `e5a6b7c8d9e0`). A part whose own narration opens with the hook — part 1, by construction — must not *play* the hook file too, or the video says the same sentence twice in a row. There the file is mounted **muted**, purely as a measure of how long that sentence takes:

| | hook played (parts 2+) | hook muted (part that opens with it) |
|---|---|---|
| who says the phrase | `hook.mp3` | the part's own narration |
| narration starts | after the hook + `tail` | with the video |
| card leaves at | `max(speech_start, hook + tail)` | end of the hook, no `tail`, no floor |
| subtitles | start with the narration | hidden while the card is up |

**Public API (pure, no `bpy`):**
- `intro_frames(default_start, hook_end=None, tail_frames=0, hook_muted=False, intro_start=1) -> (card_end, narration_start)` — the two numbers only diverge when muted. `max` and not a sum in the played case: a short hook must not *shorten* the template's intro, so `speech_start` stays the floor. No floor and **no tail** when muted — the tail separates two audio files and there is only one; measured on a real narration, charging it holds the card past the story's first word (spoken hook ends 2.560s, next word starts 2.759s) and that word loses its subtitle.
- `drop_specs_before(specs, frame) -> list` — subtitle specs from `frame` on. The card and the word-level subtitle sit at the same height, so with a muted hook (narration running *under* the card) the same sentence would print twice, stacked. Survivors are not shifted: the audio did not move.
- `card_offset_y(y_position, frame_height) -> int` — pixels from centre for `transform.offset_y`, using the same 0..1 scale as the subtitles' `y_position` (0 = bottom), clamped because an off-frame value renders as a card silently missing.

**bpy-side:**
- `add_image_strip(vse, path, channel, frame_start, frame_end)` — `fit_method="ORIGINAL"` (the PNG is authored at the exact frame width, so any fit only resamples it) and `blend_type="ALPHA_OVER"` set **explicitly**: a strip added through the API does not inherit the ALPHA_OVER the UI gives it, and without it the card's transparent margin renders as a black box over the video.
- `add_card(scene, vse, path, channel, frame_start, frame_end, config, frame_height)` — the strip plus its `blend_alpha` fade, capped at ⅓ of the strip (same reason as the subtitle fade).

**Channels** — `channels.hook` (5) and `channels.card` (6) come from `template.json` but **default in code**. `template.json` lives in the bucket: a template published before this feature has neither key, and without the default the card would land on the background's channel and cover the whole video. The card sits above the subtitles: with a played hook the two never coexist (subtitles start with the narration, by which time the card is gone), and with a muted one the subtitles under the card are dropped rather than stacked.

**Template config** (optional `card` block):
```json
"card": { "y_position": 0.5, "fade_frames": 4, "tail_seconds": 0.3 }
```
`tail_seconds` is the silence between the hook's last word and the narration's first — without it the two run together as one sentence.

`render_job` downloads both keys into the tmpdir and adds them to `job_config.json` under `assets.card` / `assets.hook`, plus `hook_muted` at the top level; a missing key is simply absent from the dict. The hook file is downloaded either way — muted or not, it is what the card is measured by. It is muted (`strip.mute = True`), not removed, so the assembled `.blend` still shows where the card's length comes from.

Verified on real renders (Blender 4.2, 1080x1920):
- **played**: 2.60s hook, narration at 3.03s (the template floor won), card 0-3.00s; mixdown shows voice at ~-22 dBFS over 0-2.5s, -37 dBFS in the gap (music bed only) and ~-19 dBFS from 3.0s.
- **muted**: narration from 0.03s, card 0-2.60s, hook strip present with `mute=True`, first subtitle at 2.77s (the story's first word — the hook's own words hidden).

- Tests: `tests/test_intro.py` (27 tests, marked `no_db` — the strip creation is exercised with fakes that record what the script asks bpy for), plus 5 in `tests/test_worker.py` for the asset plumbing and the flag.

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
"subtitles": { "fade_frames": 3, "max_hold_seconds": 0.4, "rise_frames": 4, "rise_offset": 0.025, "font_size": 160, "y_position": 0.474 }
```
`rise_offset` is a fraction of frame height (0.025 ≈ 48px at 1080×1920); `rise_frames: 0` disables the animation.

**Vertical position** — `y_position` (default `0.5`, dead centre) is a fraction of frame height, clamped to 0..1 because an off-frame value renders as subtitles silently missing rather than as an error. Strips use `align_y = "CENTER"`, so the value positions the text's own middle: the same number means the same place for a tall word and a short one. `0.05` restores the old bottom-anchored look.

The shipped template uses **0.474** — 50px below dead centre at 1920 high (`50/1920 = 0.026`). Verified by rendering the same word at the same size with only `y_position` changing: the glyph centre moved exactly 50.0px. Measure a position change that way, holding size fixed; comparing frames that differ in *both* size and position reads ~3px short, because the x-height box of a smaller font sits differently against the anchor.

⚠️ **`template.json` lives in the bucket, not in the repo.** `render_job` downloads `templates/template.json` from MinIO/R2 — editing the repo copy changes nothing until it is uploaded. These two drifted: the repo declared `font_size: 140` while the deployed template had no typography block at all, so every render used Blender's built-in 60 (measured from the rendered glyphs: 33px for "ano" against 104px at size 190). `font_size` is the only property with no code default, which is exactly why it was the one that silently regressed — font, colour and outline kept working from `DEFAULT_*`, so nothing looked broken.

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

**Per-word auto-fit** — `fit_font_size(text, font_size, max_width, measure, min_size=60)` treats `font_size` as a **ceiling**, not a fixed value: short words render at exactly that size and only the ones that would overrun the frame are scaled down, floored at `MIN_AUTOFIT_FONT_SIZE`. `SUBTITLE_SIDE_MARGIN` (0.04) keeps 4% of the width clear on each side.

Without it, raising the body size clips long words, and the clipping is **silent** — Blender does not wrap a single word and reports nothing. Measured at 1080px wide: `"procedimento,"` already occupied 1037 of 1080px at size 140, and at 170+ it ran off both edges. On a real 178-word narration, size 190 needs fitting on only 13 words (7%), the longest landing at 133.

`make_text_measurer(font_path)` builds the measuring callable from `blf`, the same rasteriser the VSE text strip uses; it returns `None` when the font cannot be loaded, and the caller then skips auto-fit rather than measuring with a typeface it will not render. Verified against a real render: blf reports 1046px for `"procedimento,"` at 140 where the rendered bounding box (outline included) is 1037px — it errs slightly wide, the safe direction for a fits-on-screen test.

`fit_font_size` is pure (the measurer is injected), so the rule is tested without Blender.

**Defaults and why:** `outline_width` is 0.24, not Blender's 0.05 — 0.05 is a hairline that vanishes over a bright frame, and past ~0.30 the outline merges between glyphs and closes the counters of round letters. `font_size` has no code default (the strip keeps Blender's 60); `template.json` sets 160, since 60 is too small for 1080×1920 — body size is a per-template design choice, not a pipeline invariant. At 160 the auto-fit touches only 2 of 178 words on a real narration. The scene's view transform must stay `Standard` (as `template.blend` has it); under `AgX` white 1.0 renders at ~0.78.

- Tests: `tests/test_subtitles.py` (62 tests total, marked `no_db` — no docker compose, no Blender needed).

### Template config endpoint (`src/blender_worker/api/routes/templates.py`)

`GET /templates/{id}/config` — downloads the template's `json_key` from MinIO and returns the parsed `template.json` as a JSON object.

Exists so **upstream services can read template settings before render time**. The orchestrator needs `narration.rate` to call the `tts_service`, which happens long before a job reaches this worker. Serving it here keeps template ownership in one place — the orchestrator never touches MinIO or parses `template.json` itself.

- `404` — template not registered
- `502` — MinIO read failed, body is not valid JSON, or the JSON is not an object

`storage/client.py` gained `download_bytes(bucket, key) -> bytes` for this (the existing `download_file` writes to disk, pointless for a config read).

Note the worker's own render path still downloads `template.json` from MinIO directly — it needs the file on disk for Blender anyway.

- Tests: `tests/test_templates.py` (13 tests; `download_bytes` mocked, DB required).

### Image text rendering (`src/blender_worker/image/text.py`)

Wraps text and computes block dimensions for the image compositor.

- `wrap_text(text, font, max_width) -> list[str]` — breaks `text` into lines that fit within `max_width` pixels. Respects explicit `\n`, never drops words that exceed `max_width` on their own.
- `measure(text, font, max_width, line_spacing=4) -> TextBlock` — calls `wrap_text` and returns a frozen `TextBlock(lines, line_height, line_spacing)`. `TextBlock.total_height` gives the full pixel height of the text block including inter-line spacing.
- Font must be a `PIL.ImageFont.FreeTypeFont` (load with `ImageFont.load_default(size=N)` for tests or `ImageFont.truetype(path, size)` for production).
- Tests: `tests/test_image_text.py` (10 tests, no DB/MinIO required).

### Image compositor (`src/blender_worker/image/composer.py`)

Composes a comment card image (rounded rect background + positioned assets + wrapped text) and returns PNG bytes. No DB or MinIO required at call time — callers are responsible for downloading assets and passing raw bytes.

**Schema (Pydantic models for the template guide JSON, guide `version: "2.0"`):** `CommentGuide`, `Canvas`, `Card`, `Background`, `Padding`, `Shadow`, `AssetSpec`, `Size`, `Position`, `TextSpec`.

**Public API:**
- `load_guide(path: Path) -> CommentGuide` — parses a guide JSON file from disk.
- `load_font(guide, root_dir) -> FreeTypeFont` — loads the TrueType font referenced by the guide.
- `compose(guide, text, asset_images, font, line_spacing=None) -> bytes` — renders and returns raw PNG bytes. `asset_images` is `dict[str, bytes]` keyed by asset `id`; missing ids are silently skipped. `line_spacing` overrides `guide.text.line_spacing`.
- `shadow_margins(shadow) -> (left, top, right, bottom)` — transparent padding the blurred shadow needs around the card. Pure, so the geometry is tested without rendering.
- `check_card_fits(guide)` — raises `ValueError` if the card plus its shadow overflows the fixed canvas width. Called at the top of `compose`.

**Canvas vs card — the two are separate.** `canvas.width` is the **output PNG width and it is fixed** (1080, matching the TikTok frame); only the height varies with the text. The card is a narrower box placed inside it at `card.offset`, and the rest of the frame stays transparent, so the PNG is meant to be dropped onto the video at full width with no positioning maths downstream. This replaces the v1 contract where `canvas.width` *was* the card and the PNG grew with the shadow.

**Card is left of centre on purpose** — `card.offset.x` (60) is smaller than the centred `(1080-880)/2 = 100`, leaving a 140px right gutter clear of TikTok's like/comment/share rail.

**Card height rule:** `padding.top + asset_row_height + gap + text_block_height + padding.bottom`. Assets stack **above** the text (a comment-card header), so the asset row and the text are summed, not `max`-ed; the `gap` is only charged when the guide declares assets.

**Fixed width means the shadow cannot grow sideways**, so `check_card_fits` rejects a card placed too close to either edge instead of letting the blur clip into a hard vertical line. This caught a real 14px overflow in the shipped template during development; `test_shipped_template_fits_its_canvas` keeps it caught.

**`canvas.supersample`** renders the whole card at N× and downsamples once with LANCZOS (the font is re-derived via `font_variant`). It is *not* what makes edges smooth — FreeType already antialiases glyphs and `_rounded_rect` already supersamples corners 4×. It is an extra uniformity pass; `supersample: 1` is a valid, faster choice. Tests assert antialiasing is present at both settings rather than claiming the knob creates it.

**Template:** `templates/comment_default.json` — 1080 canvas, 880 card at x=60, white opaque background, black **Arial Bold** at 50px, a 581×85 header strip on top (`assets/perfil-azul.png` — avatar, name and badges baked into one transparent PNG), `gap` 26, soft shadow.

⚠️ **The header is now requested above the source's own resolution.** The file is 834×122; at `supersample: 2` a 581px logical width asks for 1162px, a 1.39× upscale. It reads fine at frame size (verified on a 1080×1920 render) but the strip is no longer pixel-exact as it was at 417 — regenerating `perfil-azul.png` at ~1200px wide would restore that. Sizes and font come from the guide, so this is a JSON edit, not a code change. `assets[].size` is applied as a hard resize, so its aspect must match the file's or the image squashes silently — `test_shipped_template_asset_keeps_the_source_aspect_ratio` guards the shipped one. `font_path` is `assets/fonts/Arial-Bold.ttf` — vendored, because the image only ships `fonts-dejavu-core` (see `assets/fonts/README.md`). `Arial-Black.ttf` is vendored alongside it as a heavier alternative; it is also *wider*, so the same text wraps to more lines and the card grows taller.

- Tests: `tests/test_image_composer.py` (37 tests, marked `no_db`).

### Card drop shadow (`src/blender_worker/image/composer.py`)

Optional `background.shadow` block in the guide: `enabled` (default `False`), `color` RGBA, `blur`, `spread`, `offset` `{x, y}`. `comment_default.json` ships it on — black at 150/255, blur 14, offset y 8.

**Vertically the canvas grows to fit the shadow; horizontally it cannot.** A blurred, offset shadow falls outside the card's box. The PNG height is `margin_top + card_height + margin_bottom`, so the falloff always fits. The width is pinned at 1080, so the horizontal room has to come from `card.offset.x` and the right gutter — hence `check_card_fits`.

**Margin is `blur * BLUR_EXTENT` (3×) plus spread, adjusted by offset.** Pillow's `GaussianBlur` radius *is* the standard deviation, and ~3σ holds >99% of the kernel weight — past it the contribution is under one 8-bit alpha step. `test_shadow_does_not_clip_at_canvas_edge` asserts the whole canvas border is alpha 0, so a smaller constant fails loudly instead of degrading quietly.

**The shadow is clipped to outside the card's silhouette** (`ImageChops.subtract` against a rounded-rect occluder mask). The card background is translucent (alpha 230), so an unclipped shadow shows through it and darkens the card unevenly — strongest where the offset points. CSS `box-shadow` clips the same way.

**Off by default in the schema** so every guide written before this feature renders byte-identical geometry; only the shipped template opts in.

- `_rounded_rect(size, radius, color, scale=4)` returns the supersampled rounded rectangle; `_draw_rounded_rect(...)` composites it at a `dest`. Both are used for the card and for the shadow shape.

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
