from pydantic import BaseModel


class ImageRenderRequest(BaseModel):
    template: str
    text: str
    assets: dict[str, str] = {}
    output_key: str | None = None


class ImageRenderResponse(BaseModel):
    output_key: str
