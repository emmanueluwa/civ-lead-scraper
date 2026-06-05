FROM python:3.12-slim AS base

WORKDIR /app

RUN curl -sSL https://install.python-poetry.org | python3 - \
    && /root/.local/bin/poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock* ./

RUN /root/.local/bin/poetry install --no-interaction --no-ansi --no-root --only main

COPY . .

RUN rm -rf /app/scraper.log && mkdir -p /app/data /app/beat


FROM base AS playwright-base

RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

RUN playwright install chromium
RUN playwright install-deps chromium


FROM playwright-base AS scraper
CMD ["python", "main.py"]


FROM playwright-base AS worker
CMD ["celery", "-A", "tasks", "worker", "--loglevel=info"]


FROM playwright-base AS beat
CMD ["celery", "-A", "tasks", "beat", "--loglevel=info", "--scheduler", "celery.beat.PersistentScheduler"]


FROM base AS dashboard
EXPOSE 8000
CMD ["uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "8000"]
