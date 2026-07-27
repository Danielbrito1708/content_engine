FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Must match the Poetry that generated poetry.lock — an older Poetry rejects the
# lock with "pyproject.toml changed significantly since poetry.lock was generated".
RUN pip install --no-cache-dir poetry==2.3.3

COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --only main

COPY . .

EXPOSE 8000

CMD ["uvicorn", "src.content_scout.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
