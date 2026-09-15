# The Django-Q worker that runs experiments.
# Build context is the repository root.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.12 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
COPY deploy/start-worker.sh /app/deploy/

CMD ["sh", "/app/deploy/start-worker.sh"]
