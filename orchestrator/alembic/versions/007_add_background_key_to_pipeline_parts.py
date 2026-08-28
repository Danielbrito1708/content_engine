"""add background_key to pipeline_parts

A escolha do clipe de fundo passou a ser gravada na parte. Antes era recalculada
a cada render por `sha256(run_id) % len(keys)` — sorteio com reposição, que não
tem como saber o que já saiu e por isso repetia clipe muito antes de a
biblioteca acabar. Guardar a escolha é o que dá memória à rotação (a contagem
destas linhas diz quais clipes ainda não foram usados) e o que faz um re-render
reusar a mesma footage sem depender do cálculo.

Nula nas partes antigas de propósito: elas não entram na contagem, então o
primeiro ciclo depois desta migration passa pela biblioteca inteira.

Revision ID: 007
Revises: 006
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision: str = '007'
down_revision: str = '006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pipeline_parts', sa.Column('background_key', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('pipeline_parts', 'background_key')
