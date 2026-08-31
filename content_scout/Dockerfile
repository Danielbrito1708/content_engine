FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Alinhado com o Poetry de desenvolvimento (2.3.3). A incompatibilidade entre as
# duas versoes e de MAO UNICA, medido em 27/08/2026: o 2.3.3 le sem reclamar um
# lock gerado pelo 2.1.3, e o 2.1.3 RECUSA um lock gerado pelo 2.3.3 com
# "pyproject.toml changed significantly since poetry.lock was generated".
# Este servico ja estava em 2.3.3; os outros cinco subiram junto para que um
# `poetry lock` na maquina de desenvolvimento nao quebre build nenhum.
RUN pip install --no-cache-dir poetry==2.3.3

COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --only main

COPY . .

EXPOSE 8000

# `alembic upgrade head` antes do uvicorn, como no orchestrator. Sem isto o
# `up -d --build` sobe o código novo e deixa o banco para trás em silêncio: o
# container fica `Up`, o `/health` responde 200 — ele só pinga o DB, não a
# versão do schema — e a quebra só aparece quando o ciclo chega na escrita, até
# `interval_seconds` (3600s) depois. Aconteceu no deploy da migration 005 em
# 26/08/2026.
#
# O `&&` é a parte que importa: migration que falha tem que derrubar o boot. Um
# scout servindo com o schema errado é pior que um scout fora do ar, porque só
# o segundo é visível.
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.content_scout.api.app:app --host 0.0.0.0 --port 8000"]
