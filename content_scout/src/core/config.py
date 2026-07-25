import configparser
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic import create_model


def _infer_type(value: str) -> Any:
    lower = value.strip().lower()
    if lower in ("true", "false"):
        return lower == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value.strip()


def _create_section_model(name: str, data: dict[str, str]) -> Any:
    typed = {k: _infer_type(v) for k, v in data.items()}
    fields = {k: (type(v), v) for k, v in typed.items()}
    model_cls = create_model(name, **fields)
    return model_cls(**typed)


class ScoutEnvSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_url: str
    scout_enabled: bool
    user_agent: str

    @model_validator(mode="before")
    @classmethod
    def _from_env(cls, _data: Any) -> dict:
        if not os.environ.get("DATABASE_URL"):
            raise ValueError("Missing required environment variable: DATABASE_URL")
        return {
            "database_url": os.environ["DATABASE_URL"],
            "scout_enabled": os.environ.get("SCOUT_ENABLED", "true").lower() == "true",
            "user_agent": os.environ.get("SCOUT_USER_AGENT", "content_engine/0.1 (content_scout)"),
        }


@dataclass(frozen=True)
class Settings:
    ROOT_DIR: str
    ENV: str
    DEBUG: bool
    CONFIG: Any
    env: ScoutEnvSettings

    @classmethod
    def load(cls) -> "Settings":
        root_dir = os.environ.get("ROOT_DIR")
        if not root_dir:
            raise RuntimeError("ROOT_DIR environment variable is required")

        env = os.environ.get("ENV", "dev")
        debug = os.environ.get("DEBUG", "false").lower() == "true"

        config_file = "config.prod.ini" if env == "prod" else "config.ini"
        config_path = os.path.join(root_dir, config_file)

        parser = configparser.ConfigParser()
        parser.read(config_path)

        sections: dict[str, Any] = {}
        for section in parser.sections():
            sections[section] = _create_section_model(
                section.capitalize(), dict(parser[section])
            )

        config_cls = create_model("Config", **{k: (type(v), v) for k, v in sections.items()})
        config = config_cls(**sections)

        env_settings = ScoutEnvSettings()

        return cls(ROOT_DIR=root_dir, ENV=env, DEBUG=debug, CONFIG=config, env=env_settings)
