from datetime import datetime

from pydantic import BaseModel


class ScheduleRequest(BaseModel):
    video_key: str
    classification: dict
    part_number: int
    series_id: str


class ScheduleResponse(BaseModel):
    scheduled_at: datetime
    buffer_update_id: str
