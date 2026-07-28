"""add card_key and hook_voice_key to videos

Revision ID: d4e5f6a7b8c9
Revises: c3f1a2b4d5e6
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3f1a2b4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('videos', sa.Column('card_key', sa.String(length=512), nullable=True))
    op.add_column('videos', sa.Column('hook_voice_key', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('videos', 'hook_voice_key')
    op.drop_column('videos', 'card_key')
