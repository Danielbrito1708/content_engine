FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for Blender headless + Python build.
# ffmpeg entra pelo ffprobe: as rotas de preview da timeline declarativa
# (POST /timelines/preview/frame|clip) sondam a duração real de cada asset com
# ele, porque o Blender embute o próprio ffmpeg internamente, inacessível de
# fora. Mesmo raciocínio do orchestrator/Dockerfile para o fundo sob demanda.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    xz-utils \
    build-essential \
    libpq-dev \
    libgl1 \
    libxi6 \
    libxxf86vm1 \
    libxrender1 \
    libxfixes3 \
    libxext6 \
    libx11-6 \
    libsm6 \
    libice6 \
    libxkbcommon0 \
    fonts-dejavu-core \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Blender 4.2 LTS (headless)
RUN wget -q https://download.blender.org/release/Blender4.2/blender-4.2.20-linux-x64.tar.xz \
    && tar -xf blender-4.2.20-linux-x64.tar.xz -C /opt \
    && mv /opt/blender-4.2.20-linux-x64 /opt/blender \
    && ln -s /opt/blender/blender /usr/local/bin/blender \
    && rm blender-4.2.20-linux-x64.tar.xz

# Alinhado com o Poetry de desenvolvimento (2.3.3). A incompatibilidade entre as
# duas versoes e de MAO UNICA, medido em 27/08/2026: o 2.3.3 le sem reclamar um
# lock gerado pelo 2.1.3, e o 2.1.3 RECUSA um lock gerado pelo 2.3.3 com
# "pyproject.toml changed significantly since poetry.lock was generated".
# Com o pin antigo, qualquer `poetry lock` rodado na maquina de desenvolvimento
# quebrava este build em silencio - o erro so aparecia no `docker compose build`,
# longe da causa. Subir o pin custa nada e fecha esse caminho.
RUN pip install --no-cache-dir poetry==2.3.3

# Camada de dependências (cache quebra só se pyproject/lock mudar)
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --only main

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn src.blender_worker.api.app:app --host 0.0.0.0 --port 8000"]
