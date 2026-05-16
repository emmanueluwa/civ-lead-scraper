FROM python:3.12-slim

WORKDIR /app

# install system dependencies for Playwright
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
    && poetry install --no-interaction --no-ansi --no-root

# install Playwright browsers
RUN poetry run playwright install chromium
RUN poetry run playwright install-deps chromium

# copy application code
COPY . .

# remove scraper.log if it exists as a directory and create data dir
RUN rm -rf /app/scraper.log && mkdir -p /app/data

CMD ["python", "main.py"]
