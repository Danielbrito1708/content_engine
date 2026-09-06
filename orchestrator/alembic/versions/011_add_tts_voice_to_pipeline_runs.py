"""add tts_voice to pipeline_runs

Revision ID: 011
Revises: 010
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa


revision: str = '011'
down_revision: str = '010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pipeline_runs', sa.Column('tts_voice', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_runs', 'tts_voice')
