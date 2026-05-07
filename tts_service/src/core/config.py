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


class TTSEnvSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    tts_provider: str
    tts_voice: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    elevenlabs_api_key: str | None
    elevenlabs_voice_id: str | None

    @model_validator(mode="before")
    @classmethod
    def _from_env(cls, _data: Any) -> dict:
        required = ["MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        return {
            "tts_provider": os.environ.get("TTS_PROVIDER", "edge"),
            "tts_voice": os.environ.get("TTS_VOICE", "pt-BR-ThalitaNeural"),
            "minio_endpoint": os.environ["MINIO_ENDPOINT"],
            "minio_access_key": os.environ["MINIO_ACCESS_KEY"],
            "minio_secret_key": os.environ["MINIO_SECRET_KEY"],
            "minio_bucket": os.environ.get("MINIO_BUCKET", "blender-jobs"),
            "elevenlabs_api_key": os.environ.get("ELEVENLABS_API_KEY"),
            "elevenlabs_voice_id": os.environ.get("ELEVENLABS_VOICE_ID"),
        }

    @model_validator(mode="after")
    def _check_provider_key(self) -> "TTSEnvSettings":
        if self.tts_provider == "elevenlabs":
            if not self.elevenlabs_api_key:
                raise ValueError("ELEVENLABS_API_KEY is required when TTS_PROVIDER=elevenlabs")
            if not self.elevenlabs_voice_id:
                raise ValueError("ELEVENLABS_VOICE_ID is required when TTS_PROVIDER=elevenlabs")
        return self


@dataclass(frozen=True)
class Settings:
    ROOT_DIR: str
    ENV: str
    DEBUG: bool
    CONFIG: Any
    env: TTSEnvSettings

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

        env_settings = TTSEnvSettings()

        return cls(ROOT_DIR=root_dir, ENV=env, DEBUG=debug, CONFIG=config, env=env_settings)
