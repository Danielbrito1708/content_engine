from src.core import settings  # noqa: F401 — triggers bootstrap + load_dotenv first

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.blender_worker.api.routes import health, images, jobs, templates, timelines, videos

app = FastAPI(title="blender-worker", version="0.1.0")

# Wide open on purpose: every route here is unauthenticated already (no
# cookies, no bearer token), and the `/timelines/*` routes exist specifically
# for a browser-based editor (declarative_editor, outside this monorepo) to
# call cross-origin from wherever it's running — a dev machine's `localhost`
# port today, possibly another LAN host later. `allow_credentials=False` is
# what makes a wildcard origin valid in the first place (the two are mutually
# exclusive per the CORS spec); there being nothing to authenticate is what
# makes that an acceptable trade here, not a compromise.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(videos.router)
app.include_router(templates.router)
app.include_router(jobs.router)
app.include_router(images.router)
app.include_router(timelines.router)
