# jf-pushover-webhook

A minimal Flask service that accepts webhook calls and forwards notifications to the Pushover API. It is container-first, runs on port **8484**, and provides both a flexible webhook and a Jellyfin-compatible endpoint.

## Why use this relay instead of calling Pushover directly?

Use this relay when you need one or more of the following:
- **Centralised authentication**: callers only need to know a single `AUTH_TOKEN` for your internal webhook rather than your Pushover credentials.
- **Request filtering or enrichment**: validate, normalise or enrich incoming requests before forwarding to Pushover.
- **Network isolation or compliance**: if internal services are not permitted to reach the internet, run this relay on a host that can reach Pushover.
- **Auditability and logging**: a single place to log and observe notifications sent to Pushover.

If you fully control the caller and it can safely reach Pushover, calling their API directly is perfectly fine. This project exists for the cases where an intermediary is desirable or required.

## Features

- Two endpoints:
  - `POST /webhook` a flexible relay for generic messages.
  - `POST /jf-pushover-webhook` a Jellyfin-style endpoint with legacy alias `POST /pushover-webhook`.
- Optional Bearer auth on `/webhook` and enforced Bearer auth on `/jf-pushover-webhook` when `AUTH_TOKEN` is set.
- Dynamic Jellyfin base URL resolution from header, payload or environment.
- `/health` endpoint for liveness and readiness checks.
- Production-ready container image using `uv` and `gunicorn`.
- Offline test suite with high coverage.

## File structure

```
pushover-webhook/
├── Dockerfile
├── pyproject.toml
├── chart/                 # optional Helm chart (if used)
├── tests/
│   └── test_app.py
└── app/
    └── app.py
```

## Configuration

Set the following environment variables:
| Variable | Required | Purpose |
|---------|----------|---------|
| `PUSHOVER_API_TOKEN` | yes | Your Pushover application token |
| `PUSHOVER_USER_KEY`  | yes | Your Pushover user key |
| `AUTH_TOKEN`         | optional | If set, endpoints require `Authorization: Bearer <token>` as described below |
| `JELLYFIN_BASE_URL`  | optional | Fallback base URL for the Jellyfin image when not provided via header or payload |
| `REQUEST_TIMEOUT`    | optional | HTTP timeout in seconds, default 10 |
| `FLASK_RUN_HOST`     | optional | Dev server host, default `0.0.0.0` |
| `FLASK_RUN_PORT`     | optional | Dev server port, default `8484` |

## Endpoints

### `GET /`

Returns basic service information.

### `GET /health`

Reports `healthy` when `PUSHOVER_API_TOKEN` and `PUSHOVER_USER_KEY` are set. Otherwise returns `degraded`.

### `POST /webhook`

Generic relay for arbitrary messages. Accepts `application/json`, `application/x-www-form-urlencoded` or `text/plain` with JSON. If `AUTH_TOKEN` is set the request must include `Authorization: Bearer <token>`.

Accepted fields:
- `message` required
- `title` optional
- `image_url` optional URL of an image to attach to the Pushover message

Example:
```bash
curl -X POST "http://localhost:8484/webhook"   -H "content-type: application/json"   -d '{"message":"Hello from jf-pushover-webhook","title":"Greeting"}'
```

With auth:
```bash
curl -X POST "http://localhost:8484/webhook"   -H "authorization: Bearer ${AUTH_TOKEN}"   -H "content-type: application/json"   -d '{"message":"Hello"}'
```

### `POST /jf-pushover-webhook`  (legacy alias: `POST /pushover-webhook`)

Jellyfin-compatible endpoint that derives the Pushover title and body from the Jellyfin-style payload and fetches the primary item image to attach.

Requirements:
- `Authorization: Bearer <AUTH_TOKEN>` header is **required**.
- A Jellyfin base URL must be provided by one of:
  - `X-Jellyfin-URL` header, or
  - `ServerUrl` (or `JellyfinUrl` or `JellyfinBaseUrl`) field in the JSON payload, or
  - `JELLYFIN_BASE_URL` env variable.

Minimum payload example:
```json
{
  "ItemId": "123",
  "ItemName": "Episode 1",
  "SeriesName": "Great Show",
  "ItemType": "Episode",
  "EventId": "PlaybackStart",
  "ItemOverview": "A thrilling start"
}
```

Example:
```bash
curl -X POST "http://localhost:8484/jf-pushover-webhook"   -H "authorization: Bearer ${AUTH_TOKEN}"   -H "x-jellyfin-url: https://jellyfin.example.com"   -H "content-type: application/json"   -d @payload.json
```

## Quick start

### Docker Compose

Create `docker-compose.yml`:
```yaml
version: "3.9"
services:
  jf-pushover-webhook:
    image: ghcr.io/sudo-kraken/jf-pushover-webhook:latest
    ports:
      - "8484:8484"
    environment:
      AUTH_TOKEN: ${AUTH_TOKEN}
      PUSHOVER_API_TOKEN: ${PUSHOVER_API_TOKEN}
      PUSHOVER_USER_KEY: ${PUSHOVER_USER_KEY}
      JELLYFIN_BASE_URL: ${JELLYFIN_BASE_URL}
      # Optional
      REQUEST_TIMEOUT: ${REQUEST_TIMEOUT:-10}
      FLASK_RUN_HOST: 0.0.0.0
      FLASK_RUN_PORT: 8484
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:8484/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    restart: unless-stopped
```

Start it:
```bash
docker compose up -d
curl -s http://localhost:8484/health
```

### Docker directly

```bash
docker build -t jf-pushover-webhook:dev .
docker run --rm -p 8484:8484   -e PUSHOVER_API_TOKEN=dummy   -e PUSHOVER_USER_KEY=dummy   jf-pushover-webhook:dev
```

### Local development

Using `uv` with the Flask dev server:
```bash
export PUSHOVER_API_TOKEN=dummy
export PUSHOVER_USER_KEY=dummy
uv run python app/app.py
```

Linux or WSL using gunicorn:
```bash
uv run gunicorn -b 0.0.0.0:8484 app:app
```

Windows tip: `gunicorn` is Unix-only. On native Windows use the Flask server above or install `waitress`:
```powershell
uv add --dev waitress
$env:PUSHOVER_API_TOKEN="dummy"
$env:PUSHOVER_USER_KEY="dummy"
uv run waitress-serve --listen=0.0.0.0:8484 app:app
```

## Testing

Run the offline test suite:
```bash
uv run pytest --cov
```
