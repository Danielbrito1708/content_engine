"""initial schema: seen_items

Revision ID: 001
Revises:
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '001'
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'seen_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('external_id', sa.String(length=128), nullable=False),
        sa.Column('origin', sa.String(length=128), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('url', sa.String(length=1024), nullable=False),
        sa.Column('char_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column(
            'status',
            sa.Enum('submitted', 'filtered', 'failed', name='seenstatus'),
            nullable=False,
        ),
        sa.Column('skip_reason', sa.String(length=255), nullable=True),
        sa.Column('pipeline_run_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_seen_items_external_id', 'seen_items', ['external_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_seen_items_external_id', table_name='seen_items')
    op.drop_table('seen_items')
    sa.Enum(name='seenstatus').drop(op.get_bind(), checkfirst=True)
