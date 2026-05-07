from src.core import settings  # noqa: F401 — triggers bootstrap + load_dotenv first

from fastapi import FastAPI

from src.blender_worker.api.routes import health, images, jobs, templates, videos

app = FastAPI(title="blender-worker", version="0.1.0")

app.include_router(health.router)
app.include_router(videos.router)
app.include_router(templates.router)
app.include_router(jobs.router)
app.include_router(images.router)
