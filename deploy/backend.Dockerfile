# The public service: the Django API, serving the built SPA on the same origin.
# Build context is the repository root.
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

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
# The seed commands read ../resources (settings.RESOURCES_DIR).
COPY resources/ /app/resources/
# settings.FRONTEND_DIST_DIR.
COPY --from=frontend /app/frontend/dist /app/frontend/dist
COPY deploy/start-backend.sh /app/deploy/

RUN python manage.py collectstatic --noinput

CMD ["sh", "/app/deploy/start-backend.sh"]
