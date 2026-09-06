"""add warmup dates to account_credentials

Revision ID: 003
Revises: 002
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("account_credentials", sa.Column("tiktok_warmup_started_on", sa.Date(), nullable=True))
    op.add_column("account_credentials", sa.Column("youtube_warmup_started_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("account_credentials", "youtube_warmup_started_on")
    op.drop_column("account_credentials", "tiktok_warmup_started_on")
