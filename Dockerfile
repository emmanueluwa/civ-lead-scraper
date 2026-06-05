FROM python:3.12-slim AS base

WORKDIR /app

# install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# copy dependency files first for layer caching
COPY pyproject.toml poetry.lock* ./

# install Python dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --only main

# install Playwright browsers
RUN poetry run playwright install chromium
RUN poetry run playwright install-deps chromium

# copy application code
COPY . .

# create data directory and remove any stale log directories
RUN rm -rf /app/scraper.log && mkdir -p /app/data /app/beat

# ── scraper image ──────────────────────────────────────────────────────────
FROM base AS scraper
CMD ["python", "main.py"]

# ── worker image ───────────────────────────────────────────────────────────
FROM base AS worker
CMD ["celery", "-A", "tasks", "worker", "--loglevel=info"]

# ── beat image ─────────────────────────────────────────────────────────────
FROM base AS beat
CMD ["celery", "-A", "tasks", "beat", "--loglevel=info", "--scheduler", "celery.beat.PersistentScheduler"]

# ── dashboard image ────────────────────────────────────────────────────────
FROM base AS dashboard
EXPOSE 8000
CMD ["uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "8000"]
