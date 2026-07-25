<div align="center">
<img src="docs/assets/logo.png" align="center" width="144px" height="144px"/>

### Jellyfin PushOver Webhook

_A minimal Flask relay that accepts webhook calls and forwards notifications to Pushover. It provides a generic endpoint and a Jellyfin oriented endpoint. Built with uv and suitable for local or containerised runs._
</div>

<div align="center">

[![Docker](https://img.shields.io/github/v/tag/sudo-kraken/jf-pushover-webhook?label=docker&logo=docker&style=for-the-badge)](https://github.com/sudo-kraken/jf-pushover-webhook/pkgs/container/jf-pushover-webhook) [![Helm](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fsudo-kraken%2Fhelm-charts%2Frefs%2Fheads%2Fmain%2Fcharts%2Fjf-pushover-webhook%2FChart.yaml&query=%24.version&label=&logo=helm&style=for-the-badge&logoColor=0F1487&color=white)](https://github.com/sudo-kraken/helm-charts/tree/main/charts/jf-pushover-webhook) [![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fsudo-kraken%2Fjf-pushover-webhook%2Fmain%2Fpyproject.toml&logo=python&logoColor=yellow&color=3776AB&style=for-the-badge)](https://github.com/sudo-kraken/jf-pushover-webhook/blob/main/pyproject.toml)
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
- [Podman Quadlet](#podman-quadlet)
- [Kubernetes (Helm)](#kubernetes-helm)
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

The service accepts JSON payloads from upstream systems and relays a formatted message to Pushover. POST endpoints require a Bearer token, and outbound image hosts are controlled only through server-side configuration.

## Architecture at a glance

- Flask application with `app.app:app` WSGI target
- Two POST endpoints: generic `/webhook` and Jellyfin styled `/jf-pushover-webhook`
- Required Bearer authentication for notification delivery
- Separate liveness and configuration-readiness endpoints

## Features

- Generic webhook relay via `POST /webhook`
- Jellyfin compatible relay via `POST /jf-pushover-webhook`
- Bearer token authentication using `AUTH_TOKEN`
- Server-controlled image sources with relative request paths
- Bounded request bodies and image downloads
- Per-request and total image-download timeouts
- Simple service information page at `/`
- `/live` and `/ready` health endpoints
- Prebuilt container image on GHCR

## Prerequisites

- [Docker](https://www.docker.com/) / [Kubernetes](https://kubernetes.io/)
- Alternatively [uv](https://docs.astral.sh/uv/) and Python 3.13 for local development

## Quick start

Local development with uv

```bash
export PUSHOVER_API_TOKEN=your-app-token
export PUSHOVER_USER_KEY=your-user-key
export AUTH_TOKEN="$(openssl rand -hex 32)"
export JELLYFIN_BASE_URL=http://jellyfin.example:8096
uv sync --locked --all-extras
uv run flask --app app.app:app run --host 127.0.0.1 --port ${PORT:-8484}
```

## Docker

Pull and run

```bash
docker pull ghcr.io/sudo-kraken/jf-pushover-webhook:latest
docker run --rm -p 8484:8484 \
  -e PUSHOVER_API_TOKEN=your-app-token \
  -e PUSHOVER_USER_KEY=your-user-key \
  -e AUTH_TOKEN=replace-with-a-long-random-token \
  -e JELLYFIN_BASE_URL=http://jellyfin.example:8096 \
  ghcr.io/sudo-kraken/jf-pushover-webhook:latest
```

The supplied Compose example requires the three secret values to be present in your shell
environment. `JELLYFIN_BASE_URL` is optional but enables Jellyfin artwork:

```bash
export AUTH_TOKEN=replace-with-a-long-random-token
export PUSHOVER_API_TOKEN=your-app-token
export PUSHOVER_USER_KEY=your-user-key
export JELLYFIN_BASE_URL=http://jellyfin.example:8096
docker compose -f docker-compose.example.yml up -d
```

By default, the Compose example binds only to `127.0.0.1`. Set `BIND_ADDRESS=0.0.0.0` only when direct network exposure is intended.

## Podman Quadlet

Copy the Quadlet and its environment template into your user systemd configuration:

```bash
mkdir -p ~/.config/containers/systemd
cp podman-quadlet/pushover-webhook.container ~/.config/containers/systemd/
cp podman-quadlet/pushover-webhook.env.example \
  ~/.config/containers/systemd/pushover-webhook.env
chmod 600 ~/.config/containers/systemd/pushover-webhook.env
```

Edit `pushover-webhook.env`, then reload and start the service:

```bash
systemctl --user daemon-reload
systemctl --user start pushover-webhook.service
```

Quadlet-generated services are transient and should be started rather than enabled. If the
service must keep running after logout, enable user lingering with `loginctl enable-linger`.
The example binds to loopback and runs with a read-only root filesystem, no added capabilities,
and no-new-privileges.

## Kubernetes (Helm)

You can deploy the app on Kubernetes using the published Helm chart:

```bash
helm install jf-pushover-webhook oci://ghcr.io/sudo-kraken/helm-charts/jf-pushover-webhook \
  --namespace jf-pushover-webhook --create-namespace
```

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| PORT | no | 8484 | Gunicorn port to bind |
| WEB_CONCURRENCY | no | 2 | Gunicorn worker processes; keep at 2 or fewer to respect Pushover concurrency guidance |
| WORKER_TIMEOUT | no | 45 | Gunicorn request timeout in seconds |
| PUSHOVER_API_TOKEN | yes |  | Pushover application token |
| PUSHOVER_USER_KEY | yes |  | Pushover user key |
| AUTH_TOKEN | yes |  | Bearer token required by notification POST endpoints |
| JELLYFIN_BASE_URL | no |  | Server-controlled Jellyfin base URL used for artwork |
| IMAGE_BASE_URL | no |  | Server-controlled base URL used with `/webhook`'s relative `image_path` field |
| REQUEST_TIMEOUT | no | 10 | Connect/read timeout seconds for each outbound operation |
| IMAGE_DOWNLOAD_TIMEOUT | no | 15 | End-to-end image fetch budget in seconds; socket operations are capped to this value |
| MAX_IMAGE_BYTES | no | 5242880 | Maximum downloaded attachment size |
| MAX_REQUEST_BYTES | no | 1048576 | Maximum inbound request-body size |

`.env` example

```dotenv
PORT=8484
WEB_CONCURRENCY=2
PUSHOVER_API_TOKEN=replace-me
PUSHOVER_USER_KEY=replace-me
AUTH_TOKEN=replace-with-a-long-random-token
JELLYFIN_BASE_URL=http://jellyfin.example:8096
IMAGE_BASE_URL=https://images.example.com
REQUEST_TIMEOUT=10
IMAGE_DOWNLOAD_TIMEOUT=15
MAX_IMAGE_BYTES=5242880
MAX_REQUEST_BYTES=1048576
WORKER_TIMEOUT=45
```

## Health

- `GET /live` returns HTTP 200 when the process can serve requests.
- `GET /ready` validates required authentication, Pushover settings, and configured base URLs.
- `GET /health` is a backwards-compatible alias for `/ready`.

Readiness returns HTTP 200 when configuration is usable, or HTTP 500 with `missing` and
`invalid` variable names.

## Endpoints

- `POST /webhook` accepts JSON, form data, or JSON encoded as `text/plain`
- `POST /jf-pushover-webhook` accepts Jellyfin-style payloads and relays to Pushover
- `GET /` service information

Both POST endpoints require `Authorization: Bearer <AUTH_TOKEN>`. Jellyfin artwork is best-effort:
if the configured image is unavailable, the text notification is still sent.

Example

```bash
curl -X POST http://localhost:8484/webhook \
  -H "Authorization: Bearer ${AUTH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"title":"Backup complete","message":"All good"}'
```

For a generic attachment, configure `IMAGE_BASE_URL` and send an absolute path rather than a
full URL:

```json
{"title":"Camera","message":"Motion detected","image_path":"/events/latest.jpg"}
```

For security, payloads and headers cannot choose an outbound host, absolute `image_url` values
are rejected, and image redirects are not followed. `image_path` may contain nested path
segments made from letters, numbers, `.`, `_`, `~`, and `-`; percent-encoded or traversal
segments are rejected. Deployments upgrading from the earlier `ALLOWED_IMAGE_ORIGINS` option
should replace it with one `IMAGE_BASE_URL` and update webhook payloads to send `image_path`.

## Production notes

- Generate a long, random `AUTH_TOKEN`. For internet-facing deployments, also put the service behind an authenticating reverse proxy.
- Keep `WEB_CONCURRENCY` at 2 or fewer; Pushover asks clients not to make more than two concurrent API requests.
- Keep `REQUEST_TIMEOUT`, `IMAGE_DOWNLOAD_TIMEOUT`, and `WORKER_TIMEOUT` aligned so failed
  upstream calls cannot occupy workers indefinitely.
- Apply an outbound network policy where available.
- If running behind a reverse proxy, ensure client IP and scheme are preserved appropriately.

## Development

```bash
uv run ruff check --fix .
uv run ruff format .
uv run pytest
```

## Troubleshooting

- 401 responses usually indicate a missing or wrong `AUTH_TOKEN` when it is required.
- 504 responses indicate an outbound Pushover or image timeout; adjust timeouts only after checking connectivity.
- 400 responses for image paths mean the value is not a safe absolute path below `IMAGE_BASE_URL`.
- If payloads are rejected, confirm `Content-Type: application/json` and validate your JSON structure.

## Licence

This project is licensed under the MIT Licence. See the [LICENSE](LICENSE) file for details.

## Security

If you discover a security issue, please review and follow the guidance in [SECURITY.md](SECURITY.md), or open a private security-focused issue with minimal details and request a secure contact channel.

## Contributing

Feel free to open issues or submit pull requests if you have suggestions or improvements.  
See [CONTRIBUTING.md](CONTRIBUTING.md)

## Support

Open an [issue](/../../issues) with as much detail as possible, including your environment details and relevant logs or output.
