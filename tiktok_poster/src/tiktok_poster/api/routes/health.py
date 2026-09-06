import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.tiktok_poster.accounts.crypto import decrypt_token
from src.tiktok_poster.accounts.repository import get_credentials
from src.tiktok_poster.buffer.client import BufferClient
from src.tiktok_poster.db.engine import get_session

router = APIRouter()


@router.get("/health")
async def health(account_id: str | None = None, session: AsyncSession = Depends(get_session)):
    """Verifica a conexão com o Buffer.

    Sem `account_id`, verifica a conta default — comportamento de sempre.
    Com `account_id`, verifica **essa** conta: a checagem só é válida rodada
    com o token dono do canal (ver docs/multi_account.md). Diferente do
    `/schedule`, aqui não há vídeo renderizado em jogo — então uma conta
    pedida e não encontrada é `404`, não um silencioso "confere a default no
    lugar dela", que enganaria quem está diagnosticando justamente essa conta.
    """
    buffer = BufferClient()
    if account_id:
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Account not found")
        credentials = await get_credentials(session, account_uuid)
        if credentials is None:
            raise HTTPException(status_code=404, detail="Account not found")
        buffer = BufferClient(
            channel_id=credentials.tiktok_channel_id,
            access_token=decrypt_token(credentials.buffer_token_enc),
            org_id=credentials.buffer_org_id,
        )

    ok = await buffer.verify_connection()
    status = "ok" if ok else "degraded"
    return {"status": status, "checks": {"buffer": "ok" if ok else "unreachable"}}
