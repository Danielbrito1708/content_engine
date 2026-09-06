"""create account_credentials

Revision ID: 001
Revises:
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("buffer_token_enc", sa.Text(), nullable=False),
        sa.Column("buffer_org_id", sa.String(length=64), nullable=True),
        sa.Column("tiktok_channel_id", sa.String(length=64), nullable=False),
        sa.Column("youtube_channel_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_unique_constraint("uq_account_credentials_account_id", "account_credentials", ["account_id"])


def downgrade() -> None:
    op.drop_table("account_credentials")
