import configparser
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import structlog


def log_setup() -> None:
    parser = configparser.ConfigParser()
    parser.read("config.ini")

    log_cfg = dict(parser["log"]) if "log" in parser else {}
    level = getattr(logging, log_cfg.get("log_level", "INFO").upper(), logging.INFO)
    log_dir = log_cfg.get("log_dir", "logs")
    log_file = log_cfg.get("log_file", "app.log")
    max_bytes = int(log_cfg.get("log_max_bytes", 10_485_760))
    backup_count = int(log_cfg.get("log_backup_count", 5))

    env = os.environ.get("ENV", "dev")

    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    renderer = structlog.processors.JSONRenderer() if env == "prod" else structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared,
    )

    root = logging.getLogger()
    root.setLevel(level)

    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root.addHandler(handler)

        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, log_file),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
