# jf-pushover-webhook

Minimal Flask relay that accepts webhook calls and forwards notifications to Pushover. Provides a flexible `/webhook` and a Jellyfin-compatible endpoint. Default port 8484.

## Features
- `POST /webhook` generic relay
- `POST /jf-pushover-webhook` Jellyfin-style endpoint
- Optional Bearer auth via `AUTH_TOKEN`
- Dynamic Jellyfin base URL resolution
- `/health` endpoint
- Production container with uv and Gunicorn

## Requirements
- Python 3.13 with [uv](https://docs.astral.sh/uv/)
- Docker optional

## Quick start with uv
```bash
export PUSHOVER_API_TOKEN=dummy
export PUSHOVER_USER_KEY=dummy
uv sync --all-extras
uv run flask --app app:app run --host 0.0.0.0 --port ${PORT:-8484}
# or Gunicorn
uv run --no-dev gunicorn -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:${PORT:-8484} app:app
```

## Docker
```bash
docker run --rm -p 8484:8484 \
  -e PUSHOVER_API_TOKEN=dummy -e PUSHOVER_USER_KEY=dummy \
  ghcr.io/sudo-kraken/jf-pushover-webhook:latest

# For compose use see the repo example
```

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| PORT | no | 8484 | Port to bind |
| WEB_CONCURRENCY | no | 2 | Gunicorn workers |
| PUSHOVER_API_TOKEN | yes |  | Pushover app token |
| PUSHOVER_USER_KEY | yes |  | Pushover user key |
| AUTH_TOKEN | no |  | Bearer token required if set |
| JELLYFIN_BASE_URL | no |  | Fallback base URL for images |
| REQUEST_TIMEOUT | no | 10 | HTTP timeout seconds |

## Health and readiness
- `GET /health` reports readiness.

## Endpoints
- `POST /webhook`
- `POST /jf-pushover-webhook` and legacy alias `/pushover-webhook`
- `GET /` service info

## Project layout
```
jf-pushover-webhook/
  app/
  Dockerfile
  pyproject.toml
  tests/
```

## Development
```bash
uv run ruff check --fix .
uv run ruff format .
uv run pytest --cov
```

## Licence
See [LICENSE](LICENSE)

## Security
See [SECURITY.md](SECURITY.md)

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md)

## Support
Open an [issue](/../../issues)
