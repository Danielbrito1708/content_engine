"""story quality: hook detection + storytelling score/tag on seen_items

Revision ID: 003
Revises: 002
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa


revision: str = '003'
down_revision: str | None = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All nullable, with no default: rows written before this migration were never
    # judged, and NULL says "not judged" rather than claiming the story was weak.
    # A server_default of false on has_hook would silently label the whole backlog.
    op.add_column('seen_items', sa.Column('has_hook', sa.Boolean(), nullable=True))
    op.add_column('seen_items', sa.Column('story_score', sa.Integer(), nullable=True))
    op.add_column('seen_items', sa.Column('story_tag', sa.String(length=32), nullable=True))
    op.add_column('seen_items', sa.Column('hook_line', sa.Text(), nullable=True))
    op.add_column('seen_items', sa.Column('story_reason', sa.String(length=255), nullable=True))

    # The audit trail's whole job here is calibrating the weak/strong threshold,
    # which means grouping by tag over and over.
    op.create_index('ix_seen_items_story_tag', 'seen_items', ['story_tag'])


def downgrade() -> None:
    op.drop_index('ix_seen_items_story_tag', table_name='seen_items')
    op.drop_column('seen_items', 'story_reason')
    op.drop_column('seen_items', 'hook_line')
    op.drop_column('seen_items', 'story_tag')
    op.drop_column('seen_items', 'story_score')
    op.drop_column('seen_items', 'has_hook')
