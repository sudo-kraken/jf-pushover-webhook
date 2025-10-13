FROM ghcr.io/astral-sh/uv:0.9-python3.13-bookworm-slim@sha256:7072fbb9cf84e6b76bee43905c27a1cf4afa48bfa49de3cb2b57f748ada6cc10

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

COPY app /app

EXPOSE 8484

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8484/health || exit 1

CMD ["uv", "run", "--no-dev", "gunicorn", "-w", "2", "-b", "0.0.0.0:8484", "app:app"]
