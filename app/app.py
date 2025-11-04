import contextlib
import json
import logging
import os
import re
import tempfile

import requests
from flask import Flask, jsonify, make_response, request

# WSGI application for gunicorn: "app:app"
app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jf-pushover-webhook")

# One session for reuse and easy patching in tests
session = requests.Session()

# Timeouts
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env() -> dict[str, str | None]:
    """Read configuration from environment safely at request time."""
    return {
        "AUTH_TOKEN": os.environ.get("AUTH_TOKEN"),
        "PUSHOVER_API_TOKEN": os.environ.get("PUSHOVER_API_TOKEN"),
        "PUSHOVER_USER_KEY": os.environ.get("PUSHOVER_USER_KEY"),
        "JELLYFIN_BASE_URL": os.environ.get("JELLYFIN_BASE_URL"),
    }


def _extract_bearer_token() -> str | None:
    """Extract Bearer token from the Authorization header if present."""
    auth = request.headers.get("Authorization", "")
    m = re.match(r"Bearer\s+(.+)", auth, flags=re.IGNORECASE)
    return m.group(1) if m else None


def _require_bearer_auth(expected: str | None) -> tuple[bool, str | None]:
    """Strict Bearer auth to mirror original semantics."""
    if not expected:
        return False, "Service not configured"
    supplied = _extract_bearer_token()
    if not supplied:
        return False, "Missing or invalid Authorization header"
    return (supplied == expected), None


def _parse_payload_by_content_type() -> dict | tuple[dict, int]:
    """
    Original content type handling:
    - application/json
    - application/x-www-form-urlencoded
    - text/plain containing JSON
    Returns dict payload, or (error_json, http_status).
    """
    ct = (request.content_type or "").lower()

    if ct.startswith("application/json"):
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "Invalid JSON payload"}), 400
        return data

    if ct.startswith("application/x-www-form-urlencoded"):
        return request.form.to_dict()

    if ct.startswith("text/plain"):
        try:
            text = request.data.decode("utf-8")
            return json.loads(text)
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid JSON format in text/plain content"}), 400

    return jsonify({"error": "Unsupported Media Type", "content_type": request.content_type}), 415


def _build_title_and_body_from_jellyfin(payload: dict) -> tuple[str, str]:
    """Replicate original title and body logic from Jellyfin style fields."""
    item_name = payload.get("ItemName", "Unknown Item")
    series_name = payload.get("SeriesName", "")
    item_type = payload.get("ItemType", "Unknown Type")
    event_id = payload.get("EventId", "Unknown Event")
    item_overview = payload.get("ItemOverview", "No description provided")

    title = f"{event_id} - {series_name}: {item_name}" if series_name else f"{event_id} - {item_type}: {item_name}"

    body = item_overview
    return title, body


def _resolve_jellyfin_base_url(payload: dict) -> str | None:
    """
    Resolve the Jellyfin base URL without any hardcoded IP.
    Order of precedence:
      1) Request headers: X-Jellyfin-URL, X-Base-URL, X-External-Base-Url
      2) Payload fields: ServerUrl, JellyfinUrl, JellyfinBaseUrl, ExternalUrl
      3) Environment: JELLYFIN_BASE_URL
    Returns a string without trailing slash if found, otherwise None.
    """
    for h in ("X-Jellyfin-URL", "X-Base-URL", "X-External-Base-Url"):
        val = request.headers.get(h)
        if val:
            return val.rstrip("/")

    for k in ("ServerUrl", "JellyfinUrl", "JellyfinBaseUrl", "ExternalUrl"):
        val = payload.get(k)
        if val:
            return str(val).rstrip("/")

    env = _env()
    if env.get("JELLYFIN_BASE_URL"):
        return env["JELLYFIN_BASE_URL"].rstrip("/")

    return None


def _download_image_to_temp(image_url: str, suffix: str = ".jpg") -> str:
    """Download an image to a temporary file and return the path."""
    r = session.get(image_url, timeout=REQUEST_TIMEOUT, stream=True)
    r.raise_for_status()
    fd, temp_path = tempfile.mkstemp(prefix="jf_pushover_", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        for chunk in r.iter_content(65536):
            if chunk:
                f.write(chunk)
    return temp_path


def _send_pushover(message: str, title: str | None, img_path: str | None) -> requests.Response:
    """Send a Pushover message, optionally with an image attachment."""
    cfg = _env()
    if not cfg["PUSHOVER_API_TOKEN"] or not cfg["PUSHOVER_USER_KEY"]:
        raise RuntimeError("PUSHOVER_API_TOKEN and PUSHOVER_USER_KEY must be set")

    data = {
        "token": cfg["PUSHOVER_API_TOKEN"],
        "user": cfg["PUSHOVER_USER_KEY"],
        "message": message,
    }
    if title:
        data["title"] = title

    if img_path:
        with open(img_path, "rb") as file_handle:
            files = {"attachment": ("item_image.jpg", file_handle, "image/jpeg")}
            resp = session.post(
                "https://api.pushover.net/1/messages.json",
                data=data,
                files=files,
                timeout=REQUEST_TIMEOUT,
            )
    else:
        resp = session.post(
            "https://api.pushover.net/1/messages.json",
            data=data,
            files=None,
            timeout=REQUEST_TIMEOUT,
        )

    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# Health and landing
# ---------------------------------------------------------------------------


@app.get("/")
def index():
    return jsonify(
        {
            "service": "jf-pushover-webhook",
            "status": "ok",
            "endpoints": ["/health", "/webhook", "/jf-pushover-webhook"],
        }
    )


@app.get("/health")
def health():
    cfg = _env()
    missing = [k for k, v in cfg.items() if not v and k in ("PUSHOVER_API_TOKEN", "PUSHOVER_USER_KEY")]
    status = "healthy" if not missing else "degraded"
    return jsonify({"status": status, "missing": missing}), 200 if status == "healthy" else 500


# ---------------------------------------------------------------------------
# Flexible endpoint
# Accepts JSON or form fields like message, title, image_url, etc.
# ---------------------------------------------------------------------------


@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET":
        return jsonify(
            {
                "status": "received GET",
                "message": "Use POST with JSON or form data to send a Pushover notification",
            }
        ), 200

    cfg = _env()

    if cfg["AUTH_TOKEN"]:
        ok, err = _require_bearer_auth(cfg["AUTH_TOKEN"])
        if not ok:
            return jsonify({"error": "Unauthorised", "details": err}), 401

    data_or_error = _parse_payload_by_content_type()
    if isinstance(data_or_error, tuple):
        return data_or_error
    data: dict = data_or_error if isinstance(data_or_error, dict) else {}

    message = data.get("message") or data.get("msg") or data.get("text")
    if not message:
        return jsonify({"error": "Missing 'message'"}), 400

    title = data.get("title")
    image_url = data.get("image_url") or data.get("attachment_url")

    img_path = None
    try:
        if image_url:
            img_path = _download_image_to_temp(image_url, suffix=".img")

        resp = _send_pushover(message=message, title=title, img_path=img_path)
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        return jsonify({"status": "received POST", "pushover_response": body}), 200
    except requests.exceptions.RequestException as e:
        logger.exception("Failed to send Pushover notification")
        code = getattr(getattr(e, "response", None), "status_code", None)
        return jsonify({"error": "Failed to send Pushover notification", "details": str(e)}), 502 if code else 500
    except Exception as e:
        logger.exception("Unexpected error")
        return jsonify({"error": "Internal error", "details": str(e)}), 500
    finally:
        if img_path:
            with contextlib.suppress(Exception):
                os.remove(img_path)


@app.route("/jf-pushover-webhook", methods=["POST", "GET"])
@app.route("/pushover-webhook", methods=["POST", "GET"])  # legacy alias
def jf_pushover_webhook():
    cfg = _env()

    # Strict Bearer auth as per original
    ok, err = _require_bearer_auth(cfg["AUTH_TOKEN"])
    if not ok:
        return jsonify({"error": "Unauthorised"}), 401

    if request.method == "GET":
        resp = jsonify({"status": "received GET", "message": "This is a webhook endpoint, use POST requests"})
        # Mark legacy path usage
        if request.path.endswith("/pushover-webhook"):
            response = make_response(resp, 200)
            response.headers["X-Deprecated-Route"] = "Use /jf-pushover-webhook"
            return response
        return resp, 200

    # Parse payload using original content-type rules
    data_or_error = _parse_payload_by_content_type()
    if isinstance(data_or_error, tuple):
        return data_or_error  # error response
    data: dict = data_or_error if isinstance(data_or_error, dict) else {}

    # Build title and body exactly like the original
    title, body = _build_title_and_body_from_jellyfin(data)

    # Validate ItemId
    item_id = data.get("ItemId")
    if not item_id:
        return jsonify({"error": "Missing ItemId in payload"}), 400

    # Resolve base URL without any hardcoded default
    base_url = _resolve_jellyfin_base_url(data)
    if not base_url:
        return jsonify(
            {
                "error": "Jellyfin base URL not provided",
                "details": "Pass X-Jellyfin-URL header, a ServerUrl field in the payload, or set JELLYFIN_BASE_URL in the environment",
            }
        ), 400

    image_url = f"{base_url}/Items/{item_id}/Images/Primary"

    temp_img_path = None
    try:
        # Download image and send
        temp_img_path = _download_image_to_temp(image_url, suffix=".jpg")
        resp = _send_pushover(message=body, title=title, img_path=temp_img_path)

        # If legacy path used, add deprecation header
        if request.path.endswith("/pushover-webhook"):
            response = make_response(jsonify({"status": "received POST", "pushover_response": resp.text}), 200)
            response.headers["X-Deprecated-Route"] = "Use /jf-pushover-webhook"
            return response

        return jsonify({"status": "received POST", "pushover_response": resp.text}), 200
    except requests.exceptions.RequestException as e:
        logger.exception("Failed to process Jellyfin image or send notification")
        return jsonify({"error": "Failed to send Pushover notification", "details": str(e)}), 500
    except Exception as e:
        logger.exception("Unexpected error")
        return jsonify({"error": "Internal error", "details": str(e)}), 500
    finally:
        if temp_img_path:
            with contextlib.suppress(Exception):
                os.remove(temp_img_path)


# ---------------------------------------------------------------------------
# Dev server only. In production, gunicorn imports "app:app".
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_RUN_PORT", "8484"))
    app.run(host=host, port=port)
