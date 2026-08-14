"""add narrator_gender to pipeline_runs

Revision ID: 005
Revises: 004
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa


revision: str = '005'
down_revision: str = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pipeline_runs', sa.Column('narrator_gender', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_runs', 'narrator_gender')
