from pydantic import BaseModel


class GenerateRequest(BaseModel):
    text: str
    run_id: str
    part_number: int


class GenerateResponse(BaseModel):
    audio_key: str
