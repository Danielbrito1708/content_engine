from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    text: str
    run_id: str
    part_number: int = 1
    #: Nome do arquivo dentro do run, quando o áudio não é uma parte do roteiro
    #: (ex.: ``"hook"`` → ``audio/{run_id}/hook.mp3``). O padrão continua sendo
    #: ``part_{part_number}``. O pattern não é cosmético: a key é montada por
    #: interpolação, e um label com ``/`` ou ``..`` escreveria fora do run.
    label: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")


class GenerateResponse(BaseModel):
    audio_key: str
    srt_key: str
