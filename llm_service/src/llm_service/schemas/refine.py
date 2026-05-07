from pydantic import BaseModel, Field


class RefineRequest(BaseModel):
    script: str
    metadata: dict = {}


class TargetAudience(BaseModel):
    age_range: list[int] = Field(min_length=2, max_length=2)
    gender: str
    interests: list[str]


class Classification(BaseModel):
    content_type: str
    tone: str
    target_audience: TargetAudience
    cta_per_part: list[str]
    hashtag_hints: list[str]
    split_rationale: str | None = None


class RefineResponse(BaseModel):
    parts: list[str]
    classification: Classification
