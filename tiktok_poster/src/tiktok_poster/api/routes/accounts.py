from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.tiktok_poster.accounts.crypto import encrypt_token
from src.tiktok_poster.accounts.repository import get_credentials, list_credentials, upsert_credentials
from src.tiktok_poster.db.engine import get_session
from src.tiktok_poster.schemas.accounts import AccountCredentialsCreate, AccountCredentialsResponse

router = APIRouter(prefix="/accounts")
log = get_logger(__name__)

#: Campos opcionais de `AccountCredentialsCreate` que só devem sobrescrever a
#: linha existente quando vieram de verdade no request — ver
#: `accounts/repository.py::upsert_credentials`.
_OPTIONAL_FIELDS = (
    "buffer_org_id",
    "youtube_channel_id",
    "tiktok_warmup_started_on",
    "youtube_warmup_started_on",
)


@router.post("", response_model=AccountCredentialsResponse, status_code=201)
async def create_account_credentials(
    body: AccountCredentialsCreate,
    session: AsyncSession = Depends(get_session),
):
    """Cadastra (ou atualiza) as credenciais do Buffer de uma conta.

    Upsert por `account_id`, não criação estrita: trocar o token de uma conta
    já cadastrada (ex.: token expirado) é o mesmo endpoint, sem endpoint de
    edição separado.
    """
    existing = await get_credentials(session, body.account_id)
    provided = body.model_fields_set
    for platform, field in (("tiktok", "tiktok_channel_id"), ("youtube", "youtube_channel_id")):
        new_value = getattr(body, field)
        if (
            existing is not None
            and new_value is not None
            and new_value != getattr(existing, field)
            and getattr(existing, f"{platform}_warmup_started_on") is None
            and f"{platform}_warmup_started_on" not in provided
        ):
            log.warning(
                "canal trocado sem data de aquecimento definida",
                account_id=str(body.account_id),
                plataforma=platform,
                canal_antigo=getattr(existing, field),
                canal_novo=new_value,
            )

    row = await upsert_credentials(
        session,
        account_id=body.account_id,
        slug=body.slug,
        buffer_token_enc=encrypt_token(body.buffer_access_token),
        tiktok_channel_id=body.tiktok_channel_id,
        **{field: getattr(body, field) for field in _OPTIONAL_FIELDS if field in provided},
    )
    return row


@router.get("", response_model=list[AccountCredentialsResponse])
async def list_account_credentials(session: AsyncSession = Depends(get_session)):
    return await list_credentials(session)
