import os
from importlib import reload
from unittest.mock import MagicMock, patch

import pytest
import requests

JPEG_BYTES = b"\xff\xd8\xff\xe0test-image"
PNG_BYTES = b"\x89PNG\r\n\x1a\ntest-image"
GIF_BYTES = b"GIF89atest-image"


# Block any accidental real network
@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):  # pragma: no cover
        raise AssertionError("Network access is disabled in tests")

    monkeypatch.setattr("requests.sessions.Session.request", _blocked)


# Ensure Pushover tokens are always present unless a test deletes them explicitly
@pytest.fixture(autouse=True)
def pushover_env(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "auth-token")
    monkeypatch.setenv("PUSHOVER_API_TOKEN", "pushover-token")
    monkeypatch.setenv("PUSHOVER_USER_KEY", "pushover-user")
    for name in ("JELLYFIN_BASE_URL", "IMAGE_BASE_URL", "ALLOWED_IMAGE_ORIGINS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client():
    from app.app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        c.environ_base["HTTP_AUTHORIZATION"] = "Bearer auth-token"
        yield c


def _mock_image_get(return_bytes=JPEG_BYTES, *, status_code=200, headers=None):
    r = MagicMock()
    r.iter_content.return_value = [return_bytes]
    r.raise_for_status = MagicMock()
    r.status_code = status_code
    r.headers = {"Content-Type": "image/jpeg"} if headers is None else headers
    r.close = MagicMock()
    return r


def _mock_pushover_post(text="OK", json_body=None, status_code=200, content_type="text/plain"):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.text = text
    r.status_code = status_code
    r.headers = {"content-type": content_type}
    if json_body is not None:
        r.json.return_value = json_body
        r.headers["content-type"] = "application/json"
    return r


def _jf_payload(**overrides):
    base = {
        "ItemId": "123",
        "ItemName": "Episode 1",
        "SeriesName": "Great Show",
        "ItemType": "Episode",
        "EventId": "PlaybackStart",
        "ItemOverview": "A thrilling start",
    }
    base.update(overrides)
    return base


def test_numeric_environment_helpers_use_valid_values_and_safe_defaults(monkeypatch):
    import app.app as app_module

    monkeypatch.setenv("FLOAT_SETTING", "2.5")
    monkeypatch.setenv("INT_SETTING", "12")
    assert app_module._positive_float_from_env("FLOAT_SETTING", 1.0) == 2.5
    assert app_module._positive_int_from_env("INT_SETTING", 1) == 12

    monkeypatch.setenv("FLOAT_SETTING", "invalid")
    monkeypatch.setenv("INT_SETTING", "0")
    assert app_module._positive_float_from_env("FLOAT_SETTING", 1.0) == 1.0
    assert app_module._positive_int_from_env("INT_SETTING", 1) == 1

    monkeypatch.setenv("FLOAT_SETTING", "-1")
    monkeypatch.setenv("INT_SETTING", "invalid")
    assert app_module._positive_float_from_env("FLOAT_SETTING", 1.0) == 1.0
    assert app_module._positive_int_from_env("INT_SETTING", 1) == 1

    for invalid_value in ("nan", "inf", "-inf"):
        monkeypatch.setenv("FLOAT_SETTING", invalid_value)
        assert app_module._positive_float_from_env("FLOAT_SETTING", 1.0) == 1.0

    monkeypatch.delenv("FLOAT_SETTING")
    monkeypatch.delenv("INT_SETTING")
    assert app_module._positive_float_from_env("FLOAT_SETTING", 1.0) == 1.0
    assert app_module._positive_int_from_env("INT_SETTING", 1) == 1


def test_blank_runtime_limits_do_not_break_import(monkeypatch):
    monkeypatch.setenv("REQUEST_TIMEOUT", "")
    monkeypatch.setenv("MAX_IMAGE_BYTES", "")
    import app.app as app_module

    reload(app_module)
    assert app_module.REQUEST_TIMEOUT == 10.0
    assert app_module.MAX_IMAGE_BYTES == 5_242_880


# Index and health


def test_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["service"] == "jf-pushover-webhook"
    assert "/jf-pushover-webhook" in data["endpoints"]
    assert "/live" in data["endpoints"]
    assert "/ready" in data["endpoints"]
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_live_is_independent_of_configuration(client, monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.delenv("PUSHOVER_API_TOKEN", raising=False)
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    resp = client.get("/live")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "alive"}


def test_health_healthy(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "healthy"
    assert client.get("/ready").status_code == 200


def test_health_degraded_when_missing_tokens(monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.delenv("PUSHOVER_API_TOKEN", raising=False)
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        resp = c.get("/health")
        assert resp.status_code == 500
        assert resp.get_json()["status"] == "degraded"
        assert set(resp.get_json()["missing"]) == {
            "AUTH_TOKEN",
            "PUSHOVER_API_TOKEN",
            "PUSHOVER_USER_KEY",
        }


def test_health_rejects_blank_secrets_and_invalid_base_urls(client, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "   ")
    monkeypatch.setenv("JELLYFIN_BASE_URL", "http://jf.example?token=unsafe")
    monkeypatch.setenv("IMAGE_BASE_URL", "file:///tmp/images")

    resp = client.get("/ready")

    assert resp.status_code == 500
    assert resp.get_json()["missing"] == ["AUTH_TOKEN"]
    assert set(resp.get_json()["invalid"]) == {
        "JELLYFIN_BASE_URL",
        "IMAGE_BASE_URL",
    }


# Flexible /webhook


def test_webhook_get(client):
    r = client.get("/webhook")
    assert r.status_code == 200
    assert "Use POST" in r.get_json()["message"]


def test_webhook_post_json(client):
    with patch("app.app.session.post", return_value=_mock_pushover_post("sent")) as mock_post:
        r = client.post("/webhook", json={"message": "hello", "title": "greet"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "received POST"
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.pushover.net/1/messages.json"
        assert kwargs["data"]["message"] == "hello"
        assert kwargs["data"]["title"] == "greet"


def test_webhook_requires_bearer_when_auth_set(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    with app_module.app.test_client() as c:
        r1 = c.post("/webhook", json={"message": "x"})
        assert r1.status_code == 401
        r2 = c.post("/webhook", headers={"Authorization": "Bearer wrong"}, json={"message": "x"})
        assert r2.status_code == 401
        with patch("app.app.session.post", return_value=_mock_pushover_post("ok")) as mp:
            r3 = c.post("/webhook", headers={"Authorization": "Bearer secret"}, json={"message": "x"})
            assert r3.status_code == 200
            mp.assert_called_once()


def test_webhook_fails_closed_when_auth_is_not_configured(monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c, patch("app.app.session.post") as mock_post:
        r = c.post("/webhook", json={"message": "x"})
    assert r.status_code == 401
    assert r.get_json() == {"error": "Unauthorised"}
    mock_post.assert_not_called()


def test_webhook_rejects_malformed_bearer_value(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        r = c.post(
            "/webhook",
            headers={"Authorization": "Bearer secret trailing"},
            json={"message": "x"},
        )
    assert r.status_code == 401


def test_webhook_supports_unicode_auth_tokens(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "sëcret-token")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with (
        app_module.app.test_client() as c,
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")),
    ):
        r = c.post(
            "/webhook",
            headers={"Authorization": "Bearer sëcret-token"},
            json={"message": "x"},
        )
    assert r.status_code == 200


def test_webhook_form_and_textplain(client):
    with patch("app.app.session.post", return_value=_mock_pushover_post("ok")) as mock_post:
        r_form = client.post(
            "/webhook",
            data={"message": "form message"},
            content_type="application/x-www-form-urlencoded",
        )
        assert r_form.status_code == 200

        r_text = client.post(
            "/webhook",
            data='{"message":"text"}',
            content_type="text/plain",
        )
        assert r_text.status_code == 200

        assert mock_post.call_count == 2


def test_webhook_invalid_json_application_json(client):
    r = client.post("/webhook", data="not json", content_type="application/json")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Invalid JSON payload"


@pytest.mark.parametrize(
    ("content_type", "payload"),
    [
        ("application/json", "[]"),
        ("text/plain", '["not", "an", "object"]'),
    ],
)
def test_webhook_rejects_non_object_payloads(client, content_type, payload):
    r = client.post("/webhook", data=payload, content_type=content_type)
    assert r.status_code == 400
    assert r.get_json()["error"] == "Payload must be a JSON object"


def test_webhook_rejects_invalid_utf8_text(client):
    r = client.post("/webhook", data=b"\xff", content_type="text/plain")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Invalid JSON format in text/plain content"


def test_webhook_unsupported_media_type(client):
    r = client.post("/webhook", data="<xml/>", content_type="application/xml")
    assert r.status_code == 415
    assert r.get_json()["error"] == "Unsupported Media Type"
    assert "content_type" not in r.get_json()


def test_webhook_missing_message(client):
    r = client.post("/webhook", json={"title": "no body"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "Missing or invalid 'message'"


@pytest.mark.parametrize(
    "payload",
    [
        {"message": 123},
        {"message": "ok", "title": ["not", "text"]},
        {"message": "ok", "image_url": {"host": "example.test"}},
    ],
)
def test_webhook_rejects_invalid_field_types(client, payload):
    r = client.post("/webhook", json=payload)
    assert r.status_code == 400


@pytest.mark.parametrize(
    ("payload", "field", "limit"),
    [
        ({"message": "x" * 1_025}, "message", 1_024),
        ({"message": "ok", "title": "x" * 251}, "title", 250),
    ],
)
def test_webhook_rejects_fields_over_pushover_limits(client, payload, field, limit):
    r = client.post("/webhook", json=payload)
    assert r.status_code == 400
    assert r.get_json()["error"] == f"'{field}' must be at most {limit} characters"


def test_webhook_with_configured_image_path_and_json_response(client, monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "http://example/assets")
    with (
        patch(
            "app.app.session.get",
            return_value=_mock_image_get(PNG_BYTES, headers={"Content-Type": "image/png"}),
        ) as mock_get,
        patch("app.app.session.post", return_value=_mock_pushover_post(json_body={"status": 1})) as mock_post,
    ):
        r = client.post(
            "/webhook",
            json={"message": "img", "image_path": "/img.png?size=large"},
        )
        assert r.status_code == 200
        mock_get.assert_called_once_with(
            "http://example/assets/img.png?size=large",
            timeout=10,
            stream=True,
            allow_redirects=False,
        )
        body = r.get_json()["pushover_response"]
        assert body["status"] == 1
        attachment = mock_post.call_args.kwargs["files"]["attachment"]
        assert attachment[0] == "item_image.png"
        assert attachment[2] == "image/png"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_url", "https://internal.example/image.jpg"),
        ("image_path", "//internal.example/image.jpg"),
        ("image_path", "/../admin"),
        ("image_path", "/safe/%2e%2e/admin"),
        ("image_path", "/safe/%252e%252e/admin"),
        ("image_path", "/safe/%255cadmin"),
        ("image_path", "/safe\\admin"),
        ("image_path", "/safe.jpg#fragment"),
    ],
)
def test_webhook_rejects_request_controlled_image_authority_or_unsafe_path(
    client,
    monkeypatch,
    field,
    value,
):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://images.example")
    with patch("app.app.session.get") as mock_get:
        r = client.post("/webhook", json={"message": "x", field: value})
    assert r.status_code == 400
    assert r.get_json()["error"] == ("'image_path' must be an absolute path without traversal or a fragment")
    mock_get.assert_not_called()


def test_webhook_requires_image_base_url_for_image_paths(client):
    with patch("app.app.session.get") as mock_get:
        r = client.post("/webhook", json={"message": "x", "image_path": "/image.jpg"})
    assert r.status_code == 503
    assert r.get_json()["error"] == "IMAGE_BASE_URL is required when using 'image_path'"
    mock_get.assert_not_called()


def test_webhook_rejects_invalid_image_base_configuration(client, monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test?token=not-allowed")
    with patch("app.app.session.get") as mock_get:
        r = client.post("/webhook", json={"message": "x", "image_path": "/image.jpg"})
    assert r.status_code == 500
    assert r.get_json() == {"error": "Image source configuration is invalid"}
    mock_get.assert_not_called()


def test_webhook_image_download_http_error_returns_502(client, monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "http://bad")
    http_err = requests.exceptions.HTTPError()
    http_err.response = MagicMock(status_code=404)
    image_response = _mock_image_get()
    image_response.raise_for_status.side_effect = http_err
    with patch("app.app.session.get", return_value=image_response):
        r = client.post("/webhook", json={"message": "x", "image_path": "/img.jpg"})
    assert r.status_code == 502
    assert r.get_json() == {"error": "Image download failed"}


def test_webhook_image_download_timeout_is_distinct_from_pushover_timeout(
    client,
    monkeypatch,
):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://images.example")
    with (
        patch("app.app.session.get", side_effect=requests.exceptions.Timeout("slow")),
        patch("app.app.session.post") as mock_post,
    ):
        r = client.post("/webhook", json={"message": "x", "image_path": "/image.jpg"})
    assert r.status_code == 504
    assert r.get_json() == {"error": "Image download timed out"}
    mock_post.assert_not_called()


def test_webhook_rejects_non_image_response(client, monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test")
    image_response = _mock_image_get(headers={"Content-Type": "text/html"})
    with patch("app.app.session.get", return_value=image_response):
        r = client.post("/webhook", json={"message": "x", "image_path": "/page"})
    assert r.status_code == 422
    assert r.get_json()["error"] == "Image URL did not return a supported image"


def test_webhook_rejects_spoofed_image_content_type(client, monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test")
    image_response = _mock_image_get(
        JPEG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    with patch("app.app.session.get", return_value=image_response):
        r = client.post("/webhook", json={"message": "x", "image_path": "/spoofed.png"})
    assert r.status_code == 422
    assert r.get_json()["error"] == "Image content does not match its declared type"


def test_webhook_detects_image_type_when_header_is_missing(client, monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test")
    image_response = _mock_image_get(PNG_BYTES, headers={})
    with (
        patch("app.app.session.get", return_value=image_response),
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")) as mock_post,
    ):
        r = client.post("/webhook", json={"message": "x", "image_path": "/image"})
    assert r.status_code == 200
    assert mock_post.call_args.kwargs["files"]["attachment"][2] == "image/png"


def test_webhook_rejects_oversized_content_length(client, monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test")
    image_response = _mock_image_get(
        headers={"Content-Type": "image/jpeg", "Content-Length": "5242881"},
    )
    with (
        patch("app.app.session.get", return_value=image_response),
        patch("app.app.tempfile.mkstemp") as mock_mkstemp,
    ):
        r = client.post("/webhook", json={"message": "x", "image_path": "/large.jpg"})
    assert r.status_code == 413
    assert r.get_json()["error"] == "Image exceeds the configured size limit"
    mock_mkstemp.assert_not_called()


def test_webhook_removes_partial_file_when_stream_exceeds_limit(client, monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test")
    import app.app as app_module

    monkeypatch.setattr(app_module, "MAX_IMAGE_BYTES", 3)
    image_response = _mock_image_get()
    image_response.iter_content.return_value = [b"\xff\xd8\xff", b"d"]
    partial_path = tmp_path / "partial.img"
    file_descriptor = os.open(partial_path, os.O_CREAT | os.O_RDWR)

    with (
        patch("app.app.session.get", return_value=image_response),
        patch("app.app.tempfile.mkstemp", return_value=(file_descriptor, str(partial_path))),
    ):
        r = client.post("/webhook", json={"message": "x", "image_path": "/large.jpg"})

    assert r.status_code == 413
    assert not partial_path.exists()


def test_webhook_removes_partial_file_when_stream_fails(client, monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test")

    def failing_chunks():
        yield b"\xff\xd8\xff"
        raise requests.exceptions.ConnectionError("stream failed")

    image_response = _mock_image_get()
    image_response.iter_content.return_value = failing_chunks()
    partial_path = tmp_path / "failed.img"
    file_descriptor = os.open(partial_path, os.O_CREAT | os.O_RDWR)

    with (
        patch("app.app.session.get", return_value=image_response),
        patch("app.app.tempfile.mkstemp", return_value=(file_descriptor, str(partial_path))),
    ):
        r = client.post("/webhook", json={"message": "x", "image_path": "/image.jpg"})

    assert r.status_code == 502
    assert r.get_json() == {"error": "Image download failed"}
    assert not partial_path.exists()


def test_webhook_enforces_total_image_download_deadline(client, monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://images.example")
    import app.app as app_module

    monkeypatch.setattr(app_module, "IMAGE_DOWNLOAD_TIMEOUT", 15)
    image_response = _mock_image_get()
    partial_path = tmp_path / "timed-out.img"
    file_descriptor = os.open(partial_path, os.O_CREAT | os.O_RDWR)

    with (
        patch("app.app.session.get", return_value=image_response),
        patch("app.app.time.monotonic", side_effect=[100.0, 100.0, 116.0]),
        patch("app.app.tempfile.mkstemp", return_value=(file_descriptor, str(partial_path))),
    ):
        r = client.post("/webhook", json={"message": "x", "image_path": "/slow.jpg"})

    assert r.status_code == 504
    assert r.get_json() == {"error": "Image download exceeded the configured time limit"}
    assert not partial_path.exists()


def test_webhook_image_deadline_includes_response_headers_and_caps_socket_timeout(
    client,
    monkeypatch,
):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://images.example")
    import app.app as app_module

    monkeypatch.setattr(app_module, "IMAGE_DOWNLOAD_TIMEOUT", 5)
    monkeypatch.setattr(app_module, "REQUEST_TIMEOUT", 30)
    image_response = _mock_image_get()

    with (
        patch("app.app.session.get", return_value=image_response) as mock_get,
        patch("app.app.time.monotonic", side_effect=[100.0, 106.0]),
        patch("app.app.tempfile.mkstemp") as mock_mkstemp,
    ):
        r = client.post("/webhook", json={"message": "x", "image_path": "/slow.jpg"})

    assert r.status_code == 504
    assert r.get_json() == {"error": "Image download exceeded the configured time limit"}
    assert mock_get.call_args.kwargs["timeout"] == 5
    mock_mkstemp.assert_not_called()
    image_response.close.assert_called_once()


def test_webhook_rejects_image_redirects(client, monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "https://images.example")
    redirect_response = _mock_image_get(
        status_code=302,
        headers={"Location": "https://cdn.example/image.jpg"},
    )

    with patch("app.app.session.get", return_value=redirect_response) as mock_get:
        r = client.post("/webhook", json={"message": "x", "image_path": "/start"})

    assert r.status_code == 502
    assert r.get_json()["error"] == "Image redirects are not allowed"
    mock_get.assert_called_once()
    redirect_response.close.assert_called_once()


@pytest.mark.parametrize(
    ("signature", "content_type"),
    [
        (JPEG_BYTES, "image/jpeg"),
        (PNG_BYTES, "image/png"),
        (GIF_BYTES, "image/gif"),
        (b"not-an-image", None),
    ],
)
def test_image_type_detection_uses_file_signatures(signature, content_type):
    from app.app import _detect_image_content_type

    assert _detect_image_content_type(signature) == content_type


def test_webhook_maps_pushover_timeout_to_504(client):
    with patch("app.app.session.post", side_effect=requests.exceptions.Timeout("slow")):
        r = client.post("/webhook", json={"message": "x"})
    assert r.status_code == 504
    assert r.get_json() == {"error": "Pushover request timed out"}


@pytest.mark.parametrize(
    "json_body",
    [
        {"status": 0, "errors": ["invalid"]},
        {"request": "missing-status"},
        ["unexpected", "response"],
    ],
)
def test_webhook_rejects_unsuccessful_or_malformed_pushover_json(client, json_body):
    response = _mock_pushover_post(json_body=json_body)
    with patch("app.app.session.post", return_value=response):
        r = client.post("/webhook", json={"message": "x"})
    assert r.status_code == 502
    assert r.get_json() == {"error": "Pushover rejected the notification"}


def test_webhook_tokens_missing_returns_503_without_details(monkeypatch):
    monkeypatch.delenv("PUSHOVER_API_TOKEN", raising=False)
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c, patch("app.app.session.post", return_value=_mock_pushover_post("ok")):
        r = c.post(
            "/webhook",
            headers={"Authorization": "Bearer auth-token"},
            json={"message": "x"},
        )
        assert r.status_code == 503
        assert r.get_json() == {"error": "Service is not configured"}


def test_request_body_size_is_limited(client, monkeypatch):
    from app.app import app as flask_app

    monkeypatch.setitem(flask_app.config, "MAX_CONTENT_LENGTH", 32)
    r = client.post("/webhook", json={"message": "x" * 100})
    assert r.status_code == 413
    assert r.get_json()["error"] == "Request body exceeds the configured size limit"


# Original compatible /jf-pushover-webhook and legacy alias


def test_jf_requires_bearer_even_when_not_configured(monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        r = c.post("/jf-pushover-webhook", json=_jf_payload())
        assert r.status_code == 401
        assert r.get_json()["error"] == "Unauthorised"


def test_jf_with_configured_base_url_success(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    monkeypatch.setenv("JELLYFIN_BASE_URL", "http://jf.local")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    with (
        app_module.app.test_client() as c,
        patch("app.app.session.get", return_value=_mock_image_get()) as mock_get,
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")) as mock_post,
    ):
        r = c.post(
            "/jf-pushover-webhook",
            headers={"Authorization": "Bearer secret", "X-Jellyfin-URL": "http://jf.local/"},
            json=_jf_payload(),
        )
        assert r.status_code == 200
        mock_get.assert_called_once_with(
            "http://jf.local/Items/123/Images/Primary",
            timeout=10,
            stream=True,
            allow_redirects=False,
        )
        _, kwargs = mock_post.call_args
        assert kwargs["data"]["title"] == "PlaybackStart - Episode: Great Show - Episode 1"


def test_jf_title_without_series_uses_itemtype(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    with (
        app_module.app.test_client() as c,
        patch("app.app.session.get") as mock_get,
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")) as mock_post,
    ):
        r = c.post(
            "/jf-pushover-webhook",
            headers={"Authorization": "Bearer secret", "X-Jellyfin-URL": "http://jf.local"},
            json=_jf_payload(SeriesName="", ItemType="Movie", ItemName="Something"),
        )
        assert r.status_code == 200
        mock_get.assert_not_called()
        _, kwargs = mock_post.call_args
        assert kwargs["data"]["title"] == "PlaybackStart - Movie: Something"
        assert kwargs["files"] is None


def test_jf_generated_text_is_truncated_to_pushover_limits(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    monkeypatch.setenv("JELLYFIN_BASE_URL", "http://jf.local")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    with (
        app_module.app.test_client() as c,
        patch("app.app.session.get", return_value=_mock_image_get()),
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")) as mock_post,
    ):
        r = c.post(
            "/jf-pushover-webhook",
            headers={"Authorization": "Bearer secret"},
            json=_jf_payload(ItemName="x" * 300, ItemOverview="y" * 2_000),
        )

    assert r.status_code == 200
    _, kwargs = mock_post.call_args
    assert len(kwargs["data"]["title"]) == 250
    assert len(kwargs["data"]["message"]) == 1_024
    assert kwargs["data"]["title"].endswith("…")
    assert kwargs["data"]["message"].endswith("…")


def test_jf_request_supplied_base_urls_are_ignored(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    with (
        app_module.app.test_client() as c,
        patch("app.app.session.get") as mock_get,
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")) as mock_post,
    ):
        r = c.post(
            "/jf-pushover-webhook",
            headers={"Authorization": "Bearer secret"},
            json=_jf_payload(ServerUrl="https://jf.payload/"),
        )
        assert r.status_code == 200
        mock_get.assert_not_called()
        assert mock_post.called
        assert mock_post.call_args.kwargs["files"] is None


def test_jf_configured_base_url_takes_precedence_over_request_header(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    monkeypatch.setenv("JELLYFIN_BASE_URL", "https://jf.configured/base")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    with (
        app_module.app.test_client() as c,
        patch("app.app.session.get", return_value=_mock_image_get()) as mock_get,
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")),
    ):
        r = c.post(
            "/jf-pushover-webhook",
            headers={
                "Authorization": "Bearer secret",
                "X-Jellyfin-URL": "http://untrusted.internal",
            },
            json=_jf_payload(),
        )

    assert r.status_code == 200
    assert mock_get.call_args.args[0] == "https://jf.configured/base/Items/123/Images/Primary"


def test_jf_env_base_url_resolution(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    monkeypatch.setenv("JELLYFIN_BASE_URL", "https://jf.env")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    with (
        app_module.app.test_client() as c,
        patch("app.app.session.get", return_value=_mock_image_get()),
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")),
    ):
        r = c.post(
            "/jf-pushover-webhook",
            headers={"Authorization": "Bearer secret"},
            json=_jf_payload(),
        )
        assert r.status_code == 200


def test_jf_missing_item_id(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    monkeypatch.setenv("JELLYFIN_BASE_URL", "http://jf.example")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        payload = _jf_payload()
        payload.pop("ItemId")
        r = c.post("/jf-pushover-webhook", headers={"Authorization": "Bearer secret"}, json=payload)
        assert r.status_code == 400
        assert r.get_json()["error"] == "Missing or invalid ItemId in payload"


@pytest.mark.parametrize(
    "item_id",
    [
        "../admin?secret=true",
        "%2e%2e%2fadmin",
        "%252e%252e%252fadmin",
        "folder\\admin",
        "folder/admin",
    ],
)
def test_jf_rejects_item_ids_that_can_alter_the_image_path(monkeypatch, item_id):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    monkeypatch.setenv("JELLYFIN_BASE_URL", "http://jf.example")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with (
        app_module.app.test_client() as c,
        patch("app.app.session.get") as mock_get,
        patch("app.app.session.post") as mock_post,
    ):
        r = c.post(
            "/jf-pushover-webhook",
            headers={"Authorization": "Bearer secret"},
            json=_jf_payload(ItemId=item_id),
        )

    assert r.status_code == 400
    assert r.get_json()["error"] == "Missing or invalid ItemId in payload"
    mock_get.assert_not_called()
    mock_post.assert_not_called()


def test_jf_unsupported_media_type(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        r = c.post(
            "/jf-pushover-webhook",
            headers={"Authorization": "Bearer secret"},
            data="<xml/>",
            content_type="application/xml",
        )
        assert r.status_code == 415
        assert r.get_json()["error"] == "Unsupported Media Type"


def test_jf_legacy_alias_sets_deprecation_header(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    monkeypatch.setenv("JELLYFIN_BASE_URL", "http://jf.example")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    with (
        app_module.app.test_client() as c,
        patch("app.app.session.get", return_value=_mock_image_get()),
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")),
    ):
        r_get = c.get("/pushover-webhook", headers={"Authorization": "Bearer secret"})
        assert r_get.status_code == 200
        assert r_get.headers.get("X-Deprecated-Route") == "Use /jf-pushover-webhook"

        r_post = c.post("/pushover-webhook", headers={"Authorization": "Bearer secret"}, json=_jf_payload())
        assert r_post.status_code == 200
        assert r_post.headers.get("X-Deprecated-Route") == "Use /jf-pushover-webhook"


def test_jf_image_download_failure_sends_notification_without_attachment(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    monkeypatch.setenv("JELLYFIN_BASE_URL", "http://jf.example")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    conn_err = requests.exceptions.ConnectionError("down")
    with (
        app_module.app.test_client() as c,
        patch("app.app.session.get", side_effect=conn_err),
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")) as mock_post,
    ):
        r = c.post("/jf-pushover-webhook", headers={"Authorization": "Bearer secret"}, json=_jf_payload())
    assert r.status_code == 200
    assert mock_post.call_args.kwargs["files"] is None
