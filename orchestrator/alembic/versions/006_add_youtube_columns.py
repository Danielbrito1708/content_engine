"""add youtube title and post id

O mesmo vídeo passou a ser publicado também no YouTube: `youtube_title` guarda o
título vindo do refino (o TikTok não tem título, então não havia onde guardá-lo)
e `youtube_video_id` guarda o ID do post agendado lá, que fica nulo quando o
canal não está conectado ou quando o agendamento falhou.

Revision ID: 006
Revises: 005
Create Date: 2026-08-15
"""
from alembic import op
import sqlalchemy as sa


revision: str = '006'
down_revision: str = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pipeline_runs', sa.Column('youtube_title', sa.String(length=200), nullable=True))
    op.add_column('pipeline_parts', sa.Column('youtube_video_id', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_parts', 'youtube_video_id')
    op.drop_column('pipeline_runs', 'youtube_title')
