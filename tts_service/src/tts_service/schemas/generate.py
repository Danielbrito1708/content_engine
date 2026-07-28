from pydantic import BaseModel, field_validator

from src.core.config import validate_rate


class GenerateRequest(BaseModel):
    text: str
    run_id: str
    part_number: int
    rate: str | None = None
    """Narration speed for this request ('+15%'). Overrides TTS_RATE; None falls back to it."""

    @field_validator("rate")
    @classmethod
    def _check_rate(cls, v: str | None) -> str | None:
        return None if v is None else validate_rate(v)


class GenerateResponse(BaseModel):
    audio_key: str
    srt_key: str
