# syntax=docker/dockerfile:1

# -----------------------------
# Frontend build
# -----------------------------
FROM node:22-slim AS frontend-build

WORKDIR /build/frontend

RUN npm install --global pnpm@11.20.0 \
    && pnpm --version

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build


# -----------------------------
# VQD runtime
# -----------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/backend/.venv/bin:$PATH" \
    VQD_FRONTEND_DIST="/app/frontend/dist" \
    VQD_WORKSPACE="/data"

WORKDIR /app/backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra frameworks --no-install-project

COPY backend/ ./
RUN uv sync --frozen --no-dev --extra frameworks

COPY --from=frontend-build /build/frontend/dist /app/frontend/dist

RUN mkdir -p /data

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
