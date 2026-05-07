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


class LLMEnvSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm_provider: str
    llm_model: str
    openrouter_api_key: str | None
    chutes_api_key: str | None
    chutes_base_url: str
    anthropic_api_key: str | None

    @model_validator(mode="before")
    @classmethod
    def _from_env(cls, _data: Any) -> dict:
        return {
            "llm_provider": os.environ.get("LLM_PROVIDER", "openrouter"),
            "llm_model": os.environ.get("LLM_MODEL", "anthropic/claude-3.5-sonnet"),
            "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY"),
            "chutes_api_key": os.environ.get("CHUTES_API_KEY"),
            "chutes_base_url": os.environ.get("CHUTES_BASE_URL", "https://llm.chutes.ai/v1"),
            "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY"),
        }

    @model_validator(mode="after")
    def _check_provider_key(self) -> "LLMEnvSettings":
        if self.llm_provider == "openrouter" and not self.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
        if self.llm_provider == "chutes" and not self.chutes_api_key:
            raise ValueError("CHUTES_API_KEY is required when LLM_PROVIDER=chutes")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        return self


@dataclass(frozen=True)
class Settings:
    ROOT_DIR: str
    ENV: str
    DEBUG: bool
    CONFIG: Any
    env: LLMEnvSettings

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

        env_settings = LLMEnvSettings()

        return cls(ROOT_DIR=root_dir, ENV=env, DEBUG=debug, CONFIG=config, env=env_settings)
