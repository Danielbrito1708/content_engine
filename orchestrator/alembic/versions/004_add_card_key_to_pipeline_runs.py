"""add card_key to pipeline_runs

Revision ID: 004
Revises: 003
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa


revision: str = '004'
down_revision: str = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pipeline_runs', sa.Column('card_key', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_runs', 'card_key')
