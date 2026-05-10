"""add srt_key to pipeline_parts

Revision ID: 002
Revises: 001
Create Date: 2026-05-10
"""
from alembic import op
import sqlalchemy as sa


revision: str = '002'
down_revision: str = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pipeline_parts', sa.Column('srt_key', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_parts', 'srt_key')
