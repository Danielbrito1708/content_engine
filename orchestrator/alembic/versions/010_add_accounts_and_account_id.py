"""add accounts table and account_id to pipeline_runs

Revision ID: 010
Revises: 009
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '010'
down_revision: str = '009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('slug', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('template_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('banned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ban_reason', sa.Text(), nullable=True),
    )
    op.create_unique_constraint('uq_accounts_slug', 'accounts', ['slug'])
    op.add_column('pipeline_runs', sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_runs', 'account_id')
    op.drop_table('accounts')
