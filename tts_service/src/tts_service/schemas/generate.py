from pydantic import BaseModel, Field, field_validator

from src.core.config import validate_rate
from src.tts_service.tts.voices import normalize_gender


class GenerateRequest(BaseModel):
    text: str
    run_id: str
    part_number: int = 1
    #: Nome do arquivo dentro do run, quando o áudio não é uma parte do roteiro
    #: (ex.: ``"hook"`` → ``audio/{run_id}/hook.mp3``). O padrão continua sendo
    #: ``part_{part_number}``. O pattern não é cosmético: a key é montada por
    #: interpolação, e um label com ``/`` ou ``..`` escreveria fora do run.
    label: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    rate: str | None = None
    """Narration speed for this request ('+15%'). Overrides TTS_RATE; None falls back to it."""
    narrator_gender: str | None = None
    """Gender of the story's narrator ('male' | 'female'). Picks the voice.

    Normalised rather than rejected: it comes from an LLM classification several
    services upstream, and anything unrecognised simply means 'unknown', which
    keeps TTS_VOICE. A cosmetic field must not be able to fail a run.
    """

    @field_validator("rate")
    @classmethod
    def _check_rate(cls, v: str | None) -> str | None:
        return None if v is None else validate_rate(v)

    @field_validator("narrator_gender")
    @classmethod
    def _normalize_gender(cls, v: str | None) -> str | None:
        return None if v is None else normalize_gender(v)


class GenerateResponse(BaseModel):
    audio_key: str
    srt_key: str
    voice: str
