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
# Com o pin antigo, qualquer `poetry lock` rodado na maquina de desenvolvimento
# quebrava este build em silencio - o erro so aparecia no `docker compose build`,
# longe da causa. Subir o pin custa nada e fecha esse caminho.
RUN pip install --no-cache-dir poetry==2.3.3

COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --only main

COPY . .

EXPOSE 8000

CMD ["uvicorn", "src.tiktok_poster.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
