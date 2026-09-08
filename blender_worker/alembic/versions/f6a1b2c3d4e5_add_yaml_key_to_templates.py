"""add yaml_key to templates

Revision ID: f6a1b2c3d4e5
Revises: e5a6b7c8d9e0
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a1b2c3d4e5'
down_revision: Union[str, None] = 'e5a6b7c8d9e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'templates',
        sa.Column('yaml_key', sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('templates', 'yaml_key')
