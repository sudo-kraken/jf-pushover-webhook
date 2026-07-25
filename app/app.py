from __future__ import annotations

import contextlib
import hmac
import json
import logging
import math
import os
import re
import tempfile
import time
from urllib.parse import quote, urlsplit

import requests
from flask import Flask, jsonify, make_response, request
from werkzeug.exceptions import RequestEntityTooLarge

# WSGI application for gunicorn: "app.app:app"
app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jf-pushover-webhook")


def _positive_float_from_env(name: str, default: float) -> float:
    """Read a positive floating-point setting, falling back for blank/invalid values."""
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        value = float(raw_value)
    except ValueError:
        logger.warning("Ignoring invalid %s value; using %s", name, default)
        return default

    if not math.isfinite(value) or value <= 0:
        logger.warning("Ignoring non-positive or non-finite %s value; using %s", name, default)
        return default
    return value


def _positive_int_from_env(name: str, default: int) -> int:
    """Read a positive integer setting, falling back for blank/invalid values."""
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("Ignoring invalid %s value; using %s", name, default)
        return default

    if value <= 0:
        logger.warning("Ignoring non-positive %s value; using %s", name, default)
        return default
    return value


# One session for reuse and easy patching in tests
session = requests.Session()

# Request and attachment limits
REQUEST_TIMEOUT = _positive_float_from_env("REQUEST_TIMEOUT", 10.0)
IMAGE_DOWNLOAD_TIMEOUT = _positive_float_from_env("IMAGE_DOWNLOAD_TIMEOUT", 15.0)
PUSHOVER_MAX_IMAGE_BYTES = 5_242_880
MAX_IMAGE_BYTES = min(
    _positive_int_from_env("MAX_IMAGE_BYTES", PUSHOVER_MAX_IMAGE_BYTES),
    PUSHOVER_MAX_IMAGE_BYTES,
)
MAX_REQUEST_BYTES = _positive_int_from_env("MAX_REQUEST_BYTES", 1_048_576)
MAX_MESSAGE_CHARS = 1_024
MAX_TITLE_CHARS = 250
IMAGE_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}
IMAGE_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]+")
JELLYFIN_ITEM_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """An expected API error that can be returned without exposing internals."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _env() -> dict[str, str | None]:
    """Read configuration from environment safely at request time."""
    names = (
        "AUTH_TOKEN",
        "PUSHOVER_API_TOKEN",
        "PUSHOVER_USER_KEY",
        "JELLYFIN_BASE_URL",
        "IMAGE_BASE_URL",
    )
    values = {}
    for name in names:
        value = os.environ.get(name)
        values[name] = value.strip() if value and value.strip() else None
    return values


def _extract_bearer_token() -> str | None:
    """Extract Bearer token from the Authorization header if present."""
    auth = request.headers.get("Authorization", "")
    m = re.fullmatch(r"Bearer[ \t]+(\S+)", auth, flags=re.IGNORECASE)
    return m.group(1) if m else None


def _require_bearer_auth(expected: str | None) -> tuple[bool, str | None]:
    """Strict Bearer auth to mirror original semantics."""
    if not expected:
        return False, "Service not configured"
    supplied = _extract_bearer_token()
    if not supplied:
        return False, "Missing or invalid Authorization header"
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")), None


def _parse_payload_by_content_type() -> dict:
    """
    Parse the supported webhook content types:
    - application/json
    - application/x-www-form-urlencoded
    - text/plain containing JSON

    All accepted payloads must resolve to a JSON object.
    """
    content_type = request.mimetype or ""

    if content_type == "application/json":
        data = request.get_json(silent=True)
        if data is None:
            raise ApiError("Invalid JSON payload", 400)
    elif content_type == "application/x-www-form-urlencoded":
        data = request.form.to_dict()
    elif content_type == "text/plain":
        try:
            text = request.data.decode("utf-8")
            data = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("Invalid JSON format in text/plain content", 400) from exc
    else:
        raise ApiError("Unsupported Media Type", 415)

    if not isinstance(data, dict):
        raise ApiError("Payload must be a JSON object", 400)
    return data


def _build_title_and_body_from_jellyfin(payload: dict) -> tuple[str, str]:
    """Build bounded Pushover text from Jellyfin-style fields."""

    def payload_text(key: str, default: str) -> str:
        value = payload.get(key)
        if value is None or value == "":
            return default
        return value if isinstance(value, str) else str(value)

    item_name = payload_text("ItemName", "Unknown Item")
    series_name = payload_text("SeriesName", "")
    item_type = payload_text("ItemType", "Unknown Type")
    event_id = payload_text("EventId", "Unknown Event")
    item_overview = payload_text("ItemOverview", "No description provided")

    if series_name:
        title = f"{event_id} - {item_type}: {series_name} - {item_name}"
    else:
        title = f"{event_id} - {item_type}: {item_name}"

    return _truncate_text(title, MAX_TITLE_CHARS), _truncate_text(item_overview, MAX_MESSAGE_CHARS)


def _truncate_text(value: str, max_chars: int) -> str:
    """Truncate generated text without exceeding Pushover's character limits."""
    if len(value) <= max_chars:
        return value
    return f"{value[: max_chars - 1]}…"


def _validate_pushover_text(message: str, title: str | None) -> None:
    """Reject generic webhook fields that Pushover cannot accept."""
    if len(message) > MAX_MESSAGE_CHARS:
        raise ApiError(f"'message' must be at most {MAX_MESSAGE_CHARS} characters", 400)
    if title is not None and len(title) > MAX_TITLE_CHARS:
        raise ApiError(f"'title' must be at most {MAX_TITLE_CHARS} characters", 400)


def _build_jellyfin_image_url(base_url: str, item_id: str | int) -> str:
    """Build an image URL from server configuration and a safely encoded item ID."""
    try:
        parsed = _parse_http_url(base_url)
        if parsed.query or parsed.fragment:
            raise ValueError("Base URLs cannot include a query or fragment")
    except ValueError as exc:
        raise ApiError("Jellyfin URL configuration is invalid", 500) from exc

    encoded_item_id = quote(str(item_id), safe="")
    image_path = f"{parsed.path.rstrip('/')}/Items/{encoded_item_id}/Images/Primary"
    return parsed._replace(path=image_path, query="", fragment="").geturl()


def _parse_http_url(value: str):
    """Parse a URL and reject schemes/authorities that are unsafe for fetching."""
    if not isinstance(value, str) or not value:
        raise ValueError("URL must be a non-empty string")

    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http or https and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not supported")

    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("URL port is invalid") from exc
    return parsed


def _parse_configured_base_url(name: str, value: str):
    """Parse a server-controlled base URL without request-controlled authority data."""
    try:
        parsed = _parse_http_url(value)
        if parsed.query or parsed.fragment:
            raise ValueError("Base URLs cannot include a query or fragment")
    except ValueError as exc:
        label = "Jellyfin URL" if name == "JELLYFIN_BASE_URL" else "Image source"
        raise ApiError(f"{label} configuration is invalid", 500) from exc
    return parsed


def _build_generic_image_url(image_path: str) -> str:
    """Build an image URL using only the operator-configured authority."""
    base_url = _env()["IMAGE_BASE_URL"]
    if not base_url:
        raise ApiError("IMAGE_BASE_URL is required when using 'image_path'", 503)

    parsed_base = _parse_configured_base_url("IMAGE_BASE_URL", base_url)
    parsed_path = urlsplit(image_path)
    path_segments = parsed_path.path.lstrip("/").split("/")
    if (
        parsed_path.scheme
        or parsed_path.netloc
        or parsed_path.fragment
        or not parsed_path.path.startswith("/")
        or "%" in parsed_path.path
        or "\\" in parsed_path.path
        or not all(segment not in {".", ".."} and IMAGE_PATH_SEGMENT_RE.fullmatch(segment) for segment in path_segments)
    ):
        raise ApiError("'image_path' must be an absolute path without traversal or a fragment", 400)

    base_path = parsed_base.path.rstrip("/")
    combined_path = f"{base_path}/{parsed_path.path.lstrip('/')}"
    return parsed_base._replace(path=combined_path, query=parsed_path.query, fragment="").geturl()


def _get_image_response(image_url: str) -> requests.Response:
    """Fetch a server-built image URL without following redirects."""
    response = session.get(
        image_url,
        timeout=min(REQUEST_TIMEOUT, IMAGE_DOWNLOAD_TIMEOUT),
        stream=True,
        allow_redirects=False,
    )
    if 300 <= response.status_code < 400:
        response.close()
        raise ApiError("Image redirects are not allowed", 502)
    return response


def _detect_image_content_type(prefix: bytes) -> str | None:
    """Detect the Pushover-compatible image types from their file signatures."""
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def _download_image_to_temp(image_url: str) -> tuple[str, str]:
    """Download a bounded, time-limited image and verify its actual media type."""
    started_at = time.monotonic()
    response = _get_image_response(image_url)
    temp_path = None

    try:
        if time.monotonic() - started_at > IMAGE_DOWNLOAD_TIMEOUT:
            raise ApiError("Image download exceeded the configured time limit", 504)
        response.raise_for_status()

        declared_content_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        if declared_content_type and declared_content_type not in IMAGE_EXTENSIONS:
            raise ApiError("Image URL did not return a supported image", 422)

        content_length = response.headers.get("Content-Length")
        if content_length:
            with contextlib.suppress(ValueError):
                if int(content_length) > MAX_IMAGE_BYTES:
                    raise ApiError("Image exceeds the configured size limit", 413)

        fd, temp_path = tempfile.mkstemp(prefix="jf_pushover_", suffix=".img")
        bytes_written = 0
        signature = b""
        try:
            with os.fdopen(fd, "wb") as file_handle:
                for chunk in response.iter_content(65_536):
                    if time.monotonic() - started_at > IMAGE_DOWNLOAD_TIMEOUT:
                        raise ApiError("Image download exceeded the configured time limit", 504)
                    if not chunk:
                        continue
                    bytes_written += len(chunk)
                    if bytes_written > MAX_IMAGE_BYTES:
                        raise ApiError("Image exceeds the configured size limit", 413)
                    if len(signature) < 16:
                        signature = (signature + chunk)[:16]
                    file_handle.write(chunk)

            detected_content_type = _detect_image_content_type(signature)
            if not detected_content_type:
                raise ApiError("Image URL did not return a supported image", 422)
            if declared_content_type and declared_content_type != detected_content_type:
                raise ApiError("Image content does not match its declared type", 422)
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(temp_path)
            raise
        return temp_path, detected_content_type
    finally:
        response.close()


def _send_pushover(
    message: str,
    title: str | None,
    img_path: str | None,
    img_content_type: str | None = None,
) -> requests.Response:
    """Send a Pushover message, optionally with an image attachment."""
    cfg = _env()
    if not cfg["PUSHOVER_API_TOKEN"] or not cfg["PUSHOVER_USER_KEY"]:
        raise ApiError("Service is not configured", 503)

    data = {
        "token": cfg["PUSHOVER_API_TOKEN"],
        "user": cfg["PUSHOVER_USER_KEY"],
        "message": message,
    }
    if title:
        data["title"] = title

    if img_path:
        with open(img_path, "rb") as file_handle:
            extension = IMAGE_EXTENSIONS.get(img_content_type, ".img")
            files = {
                "attachment": (
                    f"item_image{extension}",
                    file_handle,
                    img_content_type or "application/octet-stream",
                )
            }
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


def _pushover_response_body(response: requests.Response):
    """Return a JSON response when possible and reject a non-success API status."""
    content_type = response.headers.get("content-type", "").lower()
    if content_type.startswith("application/json"):
        try:
            body = response.json()
        except requests.exceptions.JSONDecodeError:
            return response.text
        if not isinstance(body, dict) or body.get("status") != 1:
            raise ApiError("Pushover rejected the notification", 502)
        return body
    return response.text


# ---------------------------------------------------------------------------
# Health and landing
# ---------------------------------------------------------------------------


@app.errorhandler(ApiError)
def handle_api_error(error: ApiError):
    return jsonify({"error": error.message}), error.status_code


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error: RequestEntityTooLarge):
    return jsonify({"error": "Request body exceeds the configured size limit"}), 413


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.get("/")
def index():
    return jsonify(
        {
            "service": "jf-pushover-webhook",
            "status": "ok",
            "endpoints": ["/live", "/ready", "/health", "/webhook", "/jf-pushover-webhook"],
        }
    )


@app.get("/live")
def live():
    return jsonify({"status": "alive"})


@app.get("/health")
@app.get("/ready")
def health():
    cfg = _env()
    required = ("AUTH_TOKEN", "PUSHOVER_API_TOKEN", "PUSHOVER_USER_KEY")
    missing = [key for key in required if not cfg[key]]
    invalid = []
    for key in ("JELLYFIN_BASE_URL", "IMAGE_BASE_URL"):
        value = cfg[key]
        if not value:
            continue
        try:
            _parse_configured_base_url(key, value)
        except ApiError:
            invalid.append(key)

    status = "healthy" if not missing and not invalid else "degraded"
    body = {"status": status, "missing": missing, "invalid": invalid}
    return jsonify(body), 200 if status == "healthy" else 500


# ---------------------------------------------------------------------------
# Flexible endpoint
# Accepts JSON or form fields like message, title, image_path, etc.
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

    ok, _ = _require_bearer_auth(cfg["AUTH_TOKEN"])
    if not ok:
        return jsonify({"error": "Unauthorised"}), 401

    data = _parse_payload_by_content_type()

    message = data.get("message") or data.get("msg") or data.get("text")
    if not isinstance(message, str) or not message.strip():
        raise ApiError("Missing or invalid 'message'", 400)

    title = data.get("title")
    if title is not None and not isinstance(title, str):
        raise ApiError("'title' must be a string", 400)
    _validate_pushover_text(message, title)

    image_path = data.get("image_path")
    if image_path is None:
        image_path = data.get("image_url") or data.get("attachment_url")
    if image_path is not None and not isinstance(image_path, str):
        raise ApiError("'image_path' must be a string", 400)

    img_path = None
    img_content_type = None
    try:
        if image_path:
            image_url = _build_generic_image_url(image_path)
            try:
                img_path, img_content_type = _download_image_to_temp(image_url)
            except ApiError:
                raise
            except requests.exceptions.Timeout as exc:
                logger.warning("Image download timed out", exc_info=True)
                raise ApiError("Image download timed out", 504) from exc
            except requests.exceptions.RequestException as exc:
                logger.warning("Image download failed", exc_info=True)
                raise ApiError("Image download failed", 502) from exc

        resp = _send_pushover(
            message=message,
            title=title,
            img_path=img_path,
            img_content_type=img_content_type,
        )
        body = _pushover_response_body(resp)
        return jsonify({"status": "received POST", "pushover_response": body}), 200
    except ApiError:
        raise
    except requests.exceptions.Timeout as exc:
        logger.exception("Timed out while sending Pushover notification")
        raise ApiError("Pushover request timed out", 504) from exc
    except requests.exceptions.RequestException as exc:
        logger.exception("Failed to send Pushover notification")
        raise ApiError("Failed to send Pushover notification", 502) from exc
    except Exception as exc:
        logger.exception("Unexpected error")
        raise ApiError("Internal error", 500) from exc
    finally:
        if img_path:
            with contextlib.suppress(OSError):
                os.remove(img_path)


@app.route("/jf-pushover-webhook", methods=["POST", "GET"])
@app.route("/pushover-webhook", methods=["POST", "GET"])  # legacy alias
def jf_pushover_webhook():
    cfg = _env()

    # Strict Bearer auth as per original
    ok, _ = _require_bearer_auth(cfg["AUTH_TOKEN"])
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

    data = _parse_payload_by_content_type()

    # Build title and body exactly like the original
    title, body = _build_title_and_body_from_jellyfin(data)

    base_url = cfg["JELLYFIN_BASE_URL"]
    image_url = None
    if base_url:
        item_id = data.get("ItemId")
        if (
            not isinstance(item_id, (str, int))
            or isinstance(item_id, bool)
            or not JELLYFIN_ITEM_ID_RE.fullmatch(str(item_id))
        ):
            raise ApiError("Missing or invalid ItemId in payload", 400)
        image_url = _build_jellyfin_image_url(base_url, item_id)

    temp_img_path = None
    img_content_type = None
    try:
        if image_url:
            try:
                temp_img_path, img_content_type = _download_image_to_temp(image_url)
            except requests.exceptions.RequestException:
                logger.warning("Jellyfin image unavailable; sending notification without it", exc_info=True)
            except ApiError as exc:
                if exc.status_code not in {413, 422, 502, 504}:
                    raise
                logger.warning("Jellyfin image was skipped: %s", exc.message)

        resp = _send_pushover(
            message=body,
            title=title,
            img_path=temp_img_path,
            img_content_type=img_content_type,
        )
        response_body = _pushover_response_body(resp)

        # If legacy path used, add deprecation header
        if request.path.endswith("/pushover-webhook"):
            response = make_response(
                jsonify({"status": "received POST", "pushover_response": response_body}),
                200,
            )
            response.headers["X-Deprecated-Route"] = "Use /jf-pushover-webhook"
            return response

        return jsonify({"status": "received POST", "pushover_response": response_body}), 200
    except ApiError:
        raise
    except requests.exceptions.Timeout as exc:
        logger.exception("Timed out while sending Pushover notification")
        raise ApiError("Pushover request timed out", 504) from exc
    except requests.exceptions.RequestException as exc:
        logger.exception("Failed to send Pushover notification")
        raise ApiError("Failed to send Pushover notification", 502) from exc
    except Exception as exc:
        logger.exception("Unexpected error")
        raise ApiError("Internal error", 500) from exc
    finally:
        if temp_img_path:
            with contextlib.suppress(OSError):
                os.remove(temp_img_path)


# ---------------------------------------------------------------------------
# Dev server only. In production, gunicorn imports "app.app:app".
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = _positive_int_from_env("PORT", _positive_int_from_env("FLASK_RUN_PORT", 8484))
    app.run(host=host, port=port)
