FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ffmpeg entra por causa da materializacao de fundo sob demanda: o yt-dlp usa
# ele para baixar so a faixa pedida de um video, e o recorte 16:9 -> 9:16 e um
# filtro dele. Sem ffmpeg no PATH o download da faixa cai para o video inteiro.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Alinhado com o Poetry de desenvolvimento (2.3.3). A incompatibilidade entre as
# duas versoes e de MAO UNICA, medido em 27/08/2026: o 2.3.3 le sem reclamar um
# lock gerado pelo 2.1.3, e o 2.1.3 RECUSA um lock gerado pelo 2.3.3 com
# "pyproject.toml changed significantly since poetry.lock was generated".
# Com o pin antigo, qualquer `poetry lock` rodado na maquina de desenvolvimento
# quebrava este build em silencio - o erro so aparecia no `docker compose build`,
# longe da causa. Subir o pin custa nada e fecha esse caminho.
RUN pip install --no-cache-dir poetry==2.3.3

COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --only main

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn src.orchestrator.api.app:app --host 0.0.0.0 --port 8000"]
