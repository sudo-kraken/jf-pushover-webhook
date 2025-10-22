<div align="center">
<img src="docs/assets/logo.png" align="center" width="144px" height="144px"/>

### Jellyfin PushOver Webhook

_A minimal Flask relay that accepts webhook calls and forwards notifications to Pushover. It provides a generic endpoint and a Jellyfin oriented endpoint. Built with uv and suitable for local or containerised runs._
</div>

<div align="center">

[![Docker](https://img.shields.io/github/v/tag/sudo-kraken/jf-pushover-webhook?label=docker&logo=docker&style=for-the-badge)](https://github.com/sudo-kraken//jf-pushover-webhook/pkgs/container//jf-pushover-webhook) [![Helm](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fsudo-kraken%2Fhelm-charts%2Frefs%2Fheads%2Fmain%2Fcharts%2Fjf-pushover-webhook%2FChart.yaml&query=%24.version&label=&logo=helm&style=for-the-badge&logoColor=0F1487&color=white)](https://github.com/sudo-kraken/helm-charts/tree/main/charts/jf-pushover-webhook) [![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fsudo-kraken%2F/jf-pushover-webhook%2Fmain%2Fpyproject.toml&logo=python&logoColor=yellow&color=3776AB&style=for-the-badge)](https://github.com/sudo-kraken/jf-pushover-webhook/blob/main/pyproject.toml)
</div>

<div align="center">

[![OpenSSF Scorecard](https://img.shields.io/ossf-scorecard/github.com/sudo-kraken/jf-pushover-webhook?label=openssf%20scorecard&style=for-the-badge)](https://scorecard.dev/viewer/?uri=github.com/sudo-kraken/jf-pushover-webhook)

</div>

## Contents

- [Overview](#overview)
- [Architecture at a glance](#architecture-at-a-glance)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Docker](#docker)
- [Configuration](#configuration)
- [Health](#health)
- [Endpoints](#endpoints)
- [Production notes](#production-notes)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Licence](#licence)
- [Security](#security)
- [Contributing](#contributing)
- [Support](#support)

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
- Outbound request timeout control with `REQUEST_TIMEOUT`
- Simple service information page at `/`
- `/health` endpoint for liveness checks
- Prebuilt container image on GHCR

## Prerequisites

- [Docker](https://www.docker.com/) / [Kubernetes](https://kubernetes.io/)
- Alternatively [uv](https://docs.astral.sh/uv/) and Python 3.13 for local development

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
## Kubernetes (Helm)

You can deploy the app on Kubernetes using the published Helm chart:

```bash
helm install jf-pushover-webhook oci://ghcr.io/sudo-kraken/helm-charts/jf-pushover-webhook \
  --namespace jf-pushover-webhook --create-namespace
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
- Keep `REQUEST_TIMEOUT` conservative to avoid long-hanging outbound calls to Pushover.
- If running behind a reverse proxy, ensure client IP and scheme are preserved appropriately.

## Development

```bash
uv run ruff check --fix .
uv run ruff format .
uv run pytest --cov
```

## Troubleshooting

- 401 responses usually indicate a missing or wrong `AUTH_TOKEN` when it is required.
- 408 or timeouts when sending to Pushover can be reduced by increasing `REQUEST_TIMEOUT`.
- If payloads are rejected, confirm `Content-Type: application/json` and validate your JSON structure.

## Licence

This project is licensed under the MIT Licence. See the [LICENCE](LICENCE) file for details.

## Security

If you discover a security issue, please review and follow the guidance in [SECURITY.md](SECURITY.md), or open a private security-focused issue with minimal details and request a secure contact channel.

## Contributing

Feel free to open issues or submit pull requests if you have suggestions or improvements.  
See [CONTRIBUTING.md](CONTRIBUTING.md)

## Support

Open an [issue](/../../issues) with as much detail as possible, including your environment details and relevant logs or output.
