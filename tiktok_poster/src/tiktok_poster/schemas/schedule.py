from datetime import datetime

from pydantic import BaseModel, Field


class ScheduleRequest(BaseModel):
    video_key: str
    classification: dict
    part_number: int
    series_id: str
    #: Quantas partes a história tem ao todo. Vem do orchestrador, que é quem
    #: conta as partes criadas — antes era lido de `classification["parts"]`,
    #: chave que o `llm_service` nunca preencheu, então a caption dizia sempre
    #: "1/1" mesmo em série dividida.
    total_parts: int = Field(default=1, ge=1)
    #: Horário já agendado da parte anterior. Presente só em partes 2+: é o que
    #: faz a continuação sair um intervalo depois dela, em vez de cair no
    #: próximo horário livre do calendário.
    follows_at: datetime | None = None


class ScheduleResponse(BaseModel):
    scheduled_at: datetime
    buffer_update_id: str
