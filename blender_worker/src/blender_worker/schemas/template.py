import uuid
from datetime import datetime

from pydantic import BaseModel


class TemplateCreate(BaseModel):
    name: str
    blend_key: str
    json_key: str


class TemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    blend_key: str
    json_key: str
    created_at: datetime

    model_config = {"from_attributes": True}
