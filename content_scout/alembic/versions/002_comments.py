"""comment enrichment: author + comment_count on seen_items, item_comments table

Revision ID: 002
Revises: 001
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '002'
down_revision: str | None = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both nullable: rows written before this migration were never enriched, and
    # NULL says "not looked at" rather than claiming the post had no comments.
    op.add_column('seen_items', sa.Column('author', sa.String(length=128), nullable=True))
    op.add_column('seen_items', sa.Column('comment_count', sa.Integer(), nullable=True))

    op.create_table(
        'item_comments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('seen_item_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('external_id', sa.String(length=128), nullable=False),
        sa.Column('author', sa.String(length=128), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('published', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['seen_item_id'], ['seen_items.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_item_comments_seen_item_id', 'item_comments', ['seen_item_id'])


def downgrade() -> None:
    op.drop_index('ix_item_comments_seen_item_id', table_name='item_comments')
    op.drop_table('item_comments')
    op.drop_column('seen_items', 'comment_count')
    op.drop_column('seen_items', 'author')
