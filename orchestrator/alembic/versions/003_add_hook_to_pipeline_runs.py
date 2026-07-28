"""add hook, hook_audio_key and hook_srt_key to pipeline_runs

Revision ID: 003
Revises: 002
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa


revision: str = '003'
down_revision: str = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pipeline_runs', sa.Column('hook', sa.Text(), nullable=True))
    op.add_column('pipeline_runs', sa.Column('hook_audio_key', sa.String(length=512), nullable=True))
    op.add_column('pipeline_runs', sa.Column('hook_srt_key', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_runs', 'hook_srt_key')
    op.drop_column('pipeline_runs', 'hook_audio_key')
    op.drop_column('pipeline_runs', 'hook')
