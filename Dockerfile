FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_NO_CACHE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos "" --home /nonroot --uid 10001 appuser

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=appuser:appuser app ./app

USER appuser

# FLASK_RUN_PORT is retained as a backwards-compatible fallback.
ENV FLASK_RUN_PORT=8484

EXPOSE 8484

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-${FLASK_RUN_PORT:-8484}}/health" || exit 1

CMD ["sh", "-c", "exec .venv/bin/gunicorn --workers \"${WEB_CONCURRENCY:-2}\" --timeout \"${WORKER_TIMEOUT:-45}\" --bind \"0.0.0.0:${PORT:-${FLASK_RUN_PORT:-8484}}\" app.app:app"]
