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


class TikTokEnvSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    buffer_access_token: str
    buffer_profile_id: str
    buffer_org_id: str | None
    #: Canal do YouTube no mesmo Buffer. **Opcional de propósito**: vazio
    #: desliga a publicação no YouTube e o serviço segue postando só no TikTok.
    #: Sem isso, subir esta versão antes de conectar o canal derrubaria o
    #: serviço inteiro por causa de um destino secundário.
    buffer_youtube_channel_id: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    r2_public_url: str
    #: Banco `tiktok_poster`, dono da tabela `account_credentials` (Fase 1 do
    #: multi-account — ver docs/multi_account.md). Obrigatório como os demais
    #: serviços com banco próprio.
    database_url: str
    #: Chave Fernet para cifrar o token de contas extras. `None` só é aceitável
    #: enquanto nenhuma conta além da default (env vars acima) for cadastrada —
    #: a validação de verdade acontece ao cifrar/decifrar, não no boot, para não
    #: quebrar quem só usa a conta de sempre.
    account_credentials_key: str | None

    @model_validator(mode="before")
    @classmethod
    def _from_env(cls, _data: Any) -> dict:
        required = [
            "BUFFER_ACCESS_TOKEN", "BUFFER_PROFILE_ID",
            "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY",
            "DATABASE_URL",
        ]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        return {
            "buffer_access_token": os.environ["BUFFER_ACCESS_TOKEN"],
            "buffer_profile_id": os.environ["BUFFER_PROFILE_ID"],
            "buffer_org_id": os.environ.get("BUFFER_ORG_ID"),
            "buffer_youtube_channel_id": os.environ.get("BUFFER_YOUTUBE_CHANNEL_ID", ""),
            "minio_endpoint": os.environ["MINIO_ENDPOINT"],
            "minio_access_key": os.environ["MINIO_ACCESS_KEY"],
            "minio_secret_key": os.environ["MINIO_SECRET_KEY"],
            "minio_bucket": os.environ.get("MINIO_BUCKET", "blender-jobs"),
            "r2_public_url": os.environ.get("R2_PUBLIC_URL", ""),
            "database_url": os.environ["DATABASE_URL"],
            "account_credentials_key": os.environ.get("ACCOUNT_CREDENTIALS_KEY"),
        }


@dataclass(frozen=True)
class Settings:
    ROOT_DIR: str
    ENV: str
    DEBUG: bool
    CONFIG: Any
    env: TikTokEnvSettings

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

        env_settings = TikTokEnvSettings()

        return cls(ROOT_DIR=root_dir, ENV=env, DEBUG=debug, CONFIG=config, env=env_settings)
