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


class OrchestratorEnvSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_url: str
    blender_template_id: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    r2_public_url: str

    @model_validator(mode="before")
    @classmethod
    def _from_env(cls, _data: Any) -> dict:
        required = ["DATABASE_URL", "BLENDER_TEMPLATE_ID", "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        return {
            "database_url": os.environ["DATABASE_URL"],
            "blender_template_id": os.environ["BLENDER_TEMPLATE_ID"],
            "minio_endpoint": os.environ["MINIO_ENDPOINT"],
            "minio_access_key": os.environ["MINIO_ACCESS_KEY"],
            "minio_secret_key": os.environ["MINIO_SECRET_KEY"],
            "minio_bucket": os.environ.get("MINIO_BUCKET", "blender-jobs"),
            "r2_public_url": os.environ.get("R2_PUBLIC_URL", ""),
        }


@dataclass(frozen=True)
class Settings:
    ROOT_DIR: str
    ENV: str
    DEBUG: bool
    CONFIG: Any
    env: OrchestratorEnvSettings

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

        env_settings = OrchestratorEnvSettings()

        return cls(ROOT_DIR=root_dir, ENV=env, DEBUG=debug, CONFIG=config, env=env_settings)
