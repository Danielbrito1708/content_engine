import configparser
import os
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic import create_model

_RATE_RE = re.compile(r"^[+-]\d+%$")

#: Voices the narrator's gender maps to. Same neural names on `edge` and `azure`.
#: The pair is chosen to read as two people of the same age and register — what
#: changes between them is the gender, not the character. `TTS_VOICE` remains the
#: voice for a story whose narrator has no gender to match.
DEFAULT_MALE_VOICE = "pt-BR-AntonioNeural"
DEFAULT_FEMALE_VOICE = "pt-BR-FranciscaNeural"


def validate_rate(rate: str) -> str:
    """Raises ValueError unless `rate` is a signed SSML percentage ('+15%', '-10%')."""
    if not _RATE_RE.match(rate):
        raise ValueError(
            f"TTS_RATE must be a signed percentage like '+15%' or '-10%', got {rate!r}"
        )
    return rate


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
    tts_voice_male: str
    tts_voice_female: str
    tts_rate: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    azure_speech_key: str | None
    azure_speech_region: str | None
    azure_output_format: str
    elevenlabs_api_key: str | None
    elevenlabs_voice_id: str | None
    remove_silence: bool
    silence_thresh_db: int
    max_pause_ms: int
    normalize_audio: bool
    loudness_target_lufs: int
    audio_bitrate: str | None
    audio_sample_rate: int | None
    whisper_model: str
    whisper_language: str
    #: Modelo usado por ``POST /transcribe``, separado do ``whisper_model`` de
    #: propósito. A legenda transcreve áudio de TTS limpo, onde o ``base`` basta;
    #: a transcrição de vídeo de terceiro tem trilha sonora sob a voz, e ali o
    #: ``base`` erra o suficiente para atrapalhar. Um modelo maior custa ~0.4x
    #: tempo real de CPU, que só se paga no caminho que precisa dele.
    whisper_transcribe_model: str

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
            # The voice is picked per request from the narrator's gender; TTS_VOICE
            # stays the fallback for a story whose narrator has none.
            "tts_voice_male": os.environ.get("TTS_VOICE_MALE", DEFAULT_MALE_VOICE),
            "tts_voice_female": os.environ.get("TTS_VOICE_FEMALE", DEFAULT_FEMALE_VOICE),
            "tts_rate": os.environ.get("TTS_RATE", "+50%"),
            "minio_endpoint": os.environ["MINIO_ENDPOINT"],
            "minio_access_key": os.environ["MINIO_ACCESS_KEY"],
            "minio_secret_key": os.environ["MINIO_SECRET_KEY"],
            "minio_bucket": os.environ.get("MINIO_BUCKET", "blender-jobs"),
            "azure_speech_key": os.environ.get("AZURE_SPEECH_KEY"),
            "azure_speech_region": os.environ.get("AZURE_SPEECH_REGION"),
            "azure_output_format": os.environ.get(
                "AZURE_OUTPUT_FORMAT", "audio-48khz-192kbitrate-mono-mp3"
            ),
            "elevenlabs_api_key": os.environ.get("ELEVENLABS_API_KEY"),
            "elevenlabs_voice_id": os.environ.get("ELEVENLABS_VOICE_ID"),
            "remove_silence": os.environ.get("REMOVE_SILENCE", "true").lower() == "true",
            "silence_thresh_db": int(os.environ.get("SILENCE_THRESH_DB", "-40")),
            # MIN_SILENCE_MS is the former name. It always meant the same number —
            # the silence left behind at each cut — so it is honoured as a deprecated
            # alias rather than ignored, which would silently change a tuned .env.
            "max_pause_ms": int(
                os.environ.get("MAX_PAUSE_MS") or os.environ.get("MIN_SILENCE_MS") or "200"
            ),
            "normalize_audio": os.environ.get("NORMALIZE_AUDIO", "true").lower() == "true",
            "loudness_target_lufs": int(os.environ.get("LOUDNESS_TARGET_LUFS", "-16")),
            # Unset means "match the source". Forcing a rate/bitrate above what the
            # provider produced cannot add information, only file size.
            "audio_bitrate": os.environ.get("AUDIO_BITRATE") or None,
            "audio_sample_rate": (
                int(os.environ["AUDIO_SAMPLE_RATE"])
                if os.environ.get("AUDIO_SAMPLE_RATE")
                else None
            ),
            "whisper_model": os.environ.get("WHISPER_MODEL", "base"),
            "whisper_language": os.environ.get("WHISPER_LANGUAGE", "pt"),
            "whisper_transcribe_model": os.environ.get("WHISPER_TRANSCRIBE_MODEL", "small"),
        }

    @model_validator(mode="after")
    def _check_rate_format(self) -> "TTSEnvSettings":
        validate_rate(self.tts_rate)
        return self

    @model_validator(mode="after")
    def _check_provider_key(self) -> "TTSEnvSettings":
        if self.tts_provider == "azure":
            if not self.azure_speech_key:
                raise ValueError("AZURE_SPEECH_KEY is required when TTS_PROVIDER=azure")
            if not self.azure_speech_region:
                raise ValueError("AZURE_SPEECH_REGION is required when TTS_PROVIDER=azure")
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
