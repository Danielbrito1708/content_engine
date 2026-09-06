"""create publications and post_metrics

Revision ID: 002
Revises: 001
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("buffer_post_id", sa.String(length=64), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hashtag_variant", sa.String(length=255), nullable=False),
        sa.Column("timing_bucket", sa.String(length=16), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tts_voice", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Único índice, único e cobrindo a coluna ao mesmo tempo — mesmo par
    # `unique=True, index=True` do `mapped_column` em `db/models.py`; um
    # `create_unique_constraint` separado criaria um segundo índice redundante.
    op.create_index(
        "ix_publications_buffer_post_id", "publications", ["buffer_post_id"], unique=True
    )

    op.create_table(
        "post_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("metrics_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"]),
    )
    op.create_index("ix_post_metrics_publication_id", "post_metrics", ["publication_id"])


def downgrade() -> None:
    op.drop_index("ix_post_metrics_publication_id", table_name="post_metrics")
    op.drop_table("post_metrics")
    op.drop_index("ix_publications_buffer_post_id", table_name="publications")
    op.drop_table("publications")
