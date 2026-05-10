"""add blend_key to jobs

Revision ID: c3f1a2b4d5e6
Revises: 87293661c08a
Create Date: 2026-05-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f1a2b4d5e6'
down_revision: Union[str, None] = '87293661c08a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('blend_key', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'blend_key')
