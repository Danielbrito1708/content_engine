import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.tiktok_poster.db.models import AccountCredentials

#: Sentinela de "não veio no request" — distinto de `None`, que é uma
#: sobrescrita explícita (ex.: apagar `youtube_channel_id` de propósito).
#: Sem isto, reenviar `POST /accounts` só para trocar o token apagaria todo
#: campo opcional omitido do corpo, incluindo uma rampa em andamento.
UNSET = object()


async def get_credentials(session: AsyncSession, account_id: uuid.UUID) -> AccountCredentials | None:
    result = await session.execute(
        select(AccountCredentials).where(AccountCredentials.account_id == account_id)
    )
    return result.scalar_one_or_none()


async def list_credentials(session: AsyncSession) -> list[AccountCredentials]:
    result = await session.execute(
        select(AccountCredentials).order_by(AccountCredentials.created_at.desc())
    )
    return list(result.scalars().all())


async def upsert_credentials(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    slug: str,
    buffer_token_enc: str,
    tiktok_channel_id: str,
    buffer_org_id=UNSET,
    youtube_channel_id=UNSET,
    tiktok_warmup_started_on=UNSET,
    youtube_warmup_started_on=UNSET,
) -> AccountCredentials:
    """Cria ou atualiza a linha da conta.

    `slug`/`buffer_token_enc`/`tiktok_channel_id` são sempre reescritos — são
    obrigatórios no schema, então todo `POST /accounts` os manda de verdade.
    Os demais aceitam `UNSET`: omitidos do request, uma atualização **não os
    apaga** — só `None` explícito apaga. Sem essa distinção, trocar só o token
    (o caso de uso documentado do endpoint) zeraria `youtube_channel_id` e
    qualquer rampa de aquecimento em andamento sempre que o corpo não os
    repetisse.
    """
    existing = await get_credentials(session, account_id)
    if existing is not None:
        existing.slug = slug
        existing.buffer_token_enc = buffer_token_enc
        existing.tiktok_channel_id = tiktok_channel_id
        if buffer_org_id is not UNSET:
            existing.buffer_org_id = buffer_org_id
        if youtube_channel_id is not UNSET:
            existing.youtube_channel_id = youtube_channel_id
        if tiktok_warmup_started_on is not UNSET:
            existing.tiktok_warmup_started_on = tiktok_warmup_started_on
        if youtube_warmup_started_on is not UNSET:
            existing.youtube_warmup_started_on = youtube_warmup_started_on
        row = existing
    else:
        row = AccountCredentials(
            account_id=account_id,
            slug=slug,
            buffer_token_enc=buffer_token_enc,
            tiktok_channel_id=tiktok_channel_id,
            buffer_org_id=None if buffer_org_id is UNSET else buffer_org_id,
            youtube_channel_id=None if youtube_channel_id is UNSET else youtube_channel_id,
            tiktok_warmup_started_on=None if tiktok_warmup_started_on is UNSET else tiktok_warmup_started_on,
            youtube_warmup_started_on=None if youtube_warmup_started_on is UNSET else youtube_warmup_started_on,
        )
        session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
