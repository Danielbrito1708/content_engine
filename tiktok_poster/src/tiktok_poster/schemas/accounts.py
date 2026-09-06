import uuid
from datetime import date, datetime

from pydantic import BaseModel


class AccountCredentialsCreate(BaseModel):
    #: Mesmo id da conta no orchestrador (`GET /accounts` lá). Sem FK entre os
    #: dois bancos — é a aplicação que mantém os dois em sincronia pelo id.
    account_id: uuid.UUID
    slug: str
    buffer_access_token: str
    buffer_org_id: str | None = None
    tiktok_channel_id: str
    youtube_channel_id: str | None = None
    #: Data de início da rampa de aquecimento por canal. `None`/omitido =
    #: canal com histórico, ritmo cheio. Omitido numa atualização preserva o
    #: que já estava cadastrado — só `null` explícito apaga (ver
    #: `accounts/repository.py::upsert_credentials`).
    tiktok_warmup_started_on: date | None = None
    youtube_warmup_started_on: date | None = None


class AccountCredentialsResponse(BaseModel):
    #: Nunca inclui o token, cifrado ou não — só o necessário para conferir
    #: qual conta está cadastrada.
    account_id: uuid.UUID
    slug: str
    buffer_org_id: str | None
    tiktok_channel_id: str
    youtube_channel_id: str | None
    tiktok_warmup_started_on: date | None
    youtube_warmup_started_on: date | None
    created_at: datetime

    model_config = {"from_attributes": True}
