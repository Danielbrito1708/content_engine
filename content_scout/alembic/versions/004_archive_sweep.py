"""historical archive sweep: cursor table + body fingerprint on seen_items

Revision ID: 004
Revises: 003
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa


revision: str = '004'
down_revision: str | None = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable with no backfill: seen_items stores title, url and char_count but
    # never the body, so rows written before this migration cannot be fingerprinted
    # retroactively. NULL means "not fingerprinted", and the lookup ignores it —
    # repost detection therefore only covers items seen from here on.
    op.add_column('seen_items', sa.Column('content_fingerprint', sa.String(length=64), nullable=True))
    # Indexed, deliberately NOT unique. A repost has to be recorded with its own
    # audit row saying it was skipped as a duplicate; a unique constraint would
    # reject exactly that row and leave no trace of the rejection.
    op.create_index('ix_seen_items_content_fingerprint', 'seen_items', ['content_fingerprint'])

    op.create_table(
        'archive_cursors',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('origin', sa.String(length=128), nullable=False),
        sa.Column('after_id', sa.String(length=128), nullable=True),
        sa.Column('pages_read', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_swept_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('origin', name='uq_archive_cursors_origin'),
    )
    op.create_index('ix_archive_cursors_origin', 'archive_cursors', ['origin'])


def downgrade() -> None:
    op.drop_index('ix_archive_cursors_origin', table_name='archive_cursors')
    op.drop_table('archive_cursors')
    op.drop_index('ix_seen_items_content_fingerprint', table_name='seen_items')
    op.drop_column('seen_items', 'content_fingerprint')
