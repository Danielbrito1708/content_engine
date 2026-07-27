import sys

from dotenv import load_dotenv

from src.core.config import Settings
from src.core.logger import log_setup


def _init() -> Settings:
    load_dotenv()
    settings = Settings.load()
    log_setup()

    def _exception_hook(exc_type, exc_value, exc_tb):
        import structlog
        structlog.get_logger("root").critical(
            "unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _exception_hook
    return settings
