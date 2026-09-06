import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.orchestrator.db.engine import get_session
from src.orchestrator.db.models import Account
from src.orchestrator.schemas.pipeline import AccountCreate, AccountResponse

router = APIRouter(prefix="/accounts")


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(body: AccountCreate, session: AsyncSession = Depends(get_session)):
    existing = await session.execute(select(Account).where(Account.slug == body.slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Account slug already exists")

    account = Account(slug=body.slug, template_id=body.template_id, notes=body.notes)
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@router.get("", response_model=list[AccountResponse])
async def list_accounts(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Account).order_by(Account.created_at.desc()))
    return result.scalars().all()


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(account_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    account = await session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account
