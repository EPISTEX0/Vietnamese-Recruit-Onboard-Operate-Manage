FROM python:3.11-slim

WORKDIR /app/backend

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency files first for layer caching
COPY backend/pyproject.toml backend/uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Ensure config directory exists before copying
RUN mkdir -p ./config

# Copy application code & directories
COPY backend/src/ ./src/
COPY backend/alembic/ ./alembic/
COPY backend/alembic.ini ./
COPY backend/config/ ./config/
COPY backend/scripts/ ./scripts/

# Expose port
EXPOSE 8000

ENV WEB_CONCURRENCY=2

CMD ["sh", "-c", "echo 'Running migrations...' && uv run alembic upgrade head && echo 'Starting server...' && uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2} --app-dir /app/backend"]
