from pydantic import BaseModel


class ModerateRequest(BaseModel):
    text: str
    title: str = ""


class ModerateResponse(BaseModel):
    safe: bool
    category: str | None = None
    reason: str | None = None
