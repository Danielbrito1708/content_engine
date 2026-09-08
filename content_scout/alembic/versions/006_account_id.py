"""account_id: which publication account a candidate's run was sent to

Revision ID: 006
Revises: 005
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '006'
down_revision: str | None = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, no backfill: every row written before this migration really did
    # go to the default account (round-robin across accounts did not exist yet),
    # so NULL correctly means "the default account" here, same as on the
    # orchestrator's own PipelineRun.account_id.
    op.add_column('seen_items', sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index('ix_seen_items_account_id', 'seen_items', ['account_id'])


def downgrade() -> None:
    op.drop_index('ix_seen_items_account_id', table_name='seen_items')
    op.drop_column('seen_items', 'account_id')
