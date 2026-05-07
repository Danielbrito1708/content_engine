"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-07
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
        "pipeline_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_script", sa.Text(), nullable=False),
        sa.Column("input_metadata", sa.JSON(), nullable=True),
        sa.Column("refined_script", sa.Text(), nullable=True),
        sa.Column("classification", sa.JSON(), nullable=True),
        sa.Column("parts_count", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "refining", "refined", "processing",
                "scheduling", "scheduled", "posted", "failed",
                name="pipelinestatus",
            ),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "pipeline_parts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("script", sa.Text(), nullable=False),
        sa.Column("audio_key", sa.String(512), nullable=True),
        sa.Column("video_key", sa.String(512), nullable=True),
        sa.Column("blender_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "tts_running", "tts_done",
                "render_pending", "render_running", "render_done", "failed",
                name="partstatus",
            ),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tiktok_video_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("pipeline_parts")
    op.drop_table("pipeline_runs")
    op.execute("DROP TYPE IF EXISTS partstatus")
    op.execute("DROP TYPE IF EXISTS pipelinestatus")
