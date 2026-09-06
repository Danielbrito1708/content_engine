"""add mood and music_key to pipeline_runs

Revision ID: 008
Revises: 007
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision: str = '008'
down_revision: str = '007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pipeline_runs', sa.Column('mood', sa.String(length=16), nullable=True))
    op.add_column('pipeline_runs', sa.Column('music_key', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_runs', 'music_key')
    op.drop_column('pipeline_runs', 'mood')
