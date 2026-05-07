import uvicorn

from src.core import settings
from src.tts_service.api.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=settings.DEBUG)
