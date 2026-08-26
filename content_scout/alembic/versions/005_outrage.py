"""outrage: indignation score + villain flag on seen_items

Revision ID: 005
Revises: 004
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa


revision: str = '005'
down_revision: str | None = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable with no default, for the same reason migration 003 was: rows
    # written before this existed were never asked the question, and NULL says
    # "not judged" instead of claiming the story had nobody to be angry at. A
    # server_default of false on has_villain would label the whole backlog.
    op.add_column('seen_items', sa.Column('outrage_score', sa.Integer(), nullable=True))
    op.add_column('seen_items', sa.Column('has_villain', sa.Boolean(), nullable=True))

    # The ordering runs on this column, so the calibration query is "what did the
    # cycle actually see, grouped by outrage" — over and over, like story_tag.
    op.create_index('ix_seen_items_outrage_score', 'seen_items', ['outrage_score'])


def downgrade() -> None:
    op.drop_index('ix_seen_items_outrage_score', table_name='seen_items')
    op.drop_column('seen_items', 'has_villain')
    op.drop_column('seen_items', 'outrage_score')
