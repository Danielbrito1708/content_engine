import uuid
from datetime import datetime

from pydantic import BaseModel


class TemplateCreate(BaseModel):
    name: str
    blend_key: str
    json_key: str
    #: VSEL timeline object — see `db/models.py::Template.yaml_key`. Optional
    #: at creation so a row can still be made before the YAML is published;
    #: `worker.py` refuses to render a template that never gets one.
    yaml_key: str | None = None


class TemplateUpdate(BaseModel):
    """`PATCH /templates/{id}` — every field optional, only what's passed
    changes. Exists for retrofitting the VSEL cutover onto an existing
    production `Template` row (set `yaml_key` once the YAML is published to
    the bucket) without losing the `id` every other row already points at
    (`BLENDER_TEMPLATE_ID`, `Video.template_id` foreign keys)."""

    name: str | None = None
    blend_key: str | None = None
    json_key: str | None = None
    yaml_key: str | None = None


class TemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    blend_key: str
    json_key: str
    yaml_key: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
