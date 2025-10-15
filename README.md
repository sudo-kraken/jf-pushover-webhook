# jf-pushover-webhook

A minimal Flask relay that accepts webhook calls and forwards notifications to Pushover. It provides a generic endpoint and a Jellyfin oriented endpoint. Built with uv and suitable for local or containerised runs.

## Overview

The service accepts JSON payloads from upstream systems and relays a formatted message to Pushover. A basic Bearer token can be used to restrict access. A health endpoint is provided for liveness checks.

## Architecture at a glance

- Flask app factory with `app:app` WSGI target
- Two POST endpoints: generic `/webhook` and Jellyfin styled `/jf-pushover-webhook`
- Optional Bearer authentication
- Health endpoint `GET /health`

## Features

- Generic webhook relay via `POST /webhook`
- Jellyfin compatible relay via `POST /jf-pushover-webhook`
- Optional Bearer token authentication using `AUTH_TOKEN`
- Outbound request timeout control
- Simple service information page at `/`
- `/health` endpoint for liveness checks
- Prebuilt container image on GHCR

## Prerequisites

- [Docker](https://www.docker.com/)
- (Alternatively) [uv](https://docs.astral.sh/uv/) and Python 3.13 for local development

## Quick start

Local development with uv

```bash
export PUSHOVER_API_TOKEN=your-app-token
export PUSHOVER_USER_KEY=your-user-key
uv sync --all-extras
uv run flask --app app:app run --host 0.0.0.0 --port ${PORT:-8484}
```

## Docker

Pull and run

```bash
docker pull ghcr.io/sudo-kraken/jf-pushover-webhook:latest
docker run --rm -p 8484:8484 \
  -e PUSHOVER_API_TOKEN=your-app-token \
  -e PUSHOVER_USER_KEY=your-user-key \
  ghcr.io/sudo-kraken/jf-pushover-webhook:latest
```

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| PORT | no | 8484 | Port to bind |
| WEB_CONCURRENCY | no | 2 | Gunicorn worker processes |
| PUSHOVER_API_TOKEN | yes |  | Pushover application token |
| PUSHOVER_USER_KEY | yes |  | Pushover user key |
| AUTH_TOKEN | no |  | Bearer token required if set |
| JELLYFIN_BASE_URL | no |  | Fallback base URL for images |
| REQUEST_TIMEOUT | no | 10 | HTTP timeout seconds for outbound calls |

`.env` example

```dotenv
PORT=8484
WEB_CONCURRENCY=2
PUSHOVER_API_TOKEN=replace-me
PUSHOVER_USER_KEY=replace-me
AUTH_TOKEN=optional-bearer
REQUEST_TIMEOUT=10
```

## Health

- `GET /health` returns `{ "ok": true }`

## Endpoints

- `POST /webhook` accepts JSON payloads and sends a Pushover message
- `POST /jf-pushover-webhook` accepts Jellyfin style payloads and relays to Pushover
- `GET /` service information

Example

```bash
curl -X POST http://localhost:8484/webhook \
  -H "Content-Type: application/json" \
  -d '{"title":"Backup complete","message":"All good"}'
```

## Production notes

- Set `AUTH_TOKEN` for a simple shared secret. For internet facing deployments put the service behind an authenticating reverse proxy.
- Tune `WEB_CONCURRENCY` based on CPU cores and expected throughput.

## Development

```bash
uv run ruff check --fix .
uv run ruff format .
uv run pytest --cov
```

## Troubleshooting

- 401 responses usually indicate a missing or wrong `AUTH_TOKEN` when it is required.
- 408 or timeouts when sending to Pushover can be reduced by increasing `REQUEST_TIMEOUT`.

## Licence
See [LICENSE](LICENSE)

## Security
See [SECURITY.md](SECURITY.md)

## Contributing
Feel free to open issues or submit pull requests if you have suggestions or improvements.
See [CONTRIBUTING.md](CONTRIBUTING.md)

## Support
Open an [issue](/../../issues)
