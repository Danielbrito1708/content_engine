import uvicorn
from src.core import settings

if __name__ == "__main__":
    uvicorn.run(
        "src.blender_worker.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
    )
