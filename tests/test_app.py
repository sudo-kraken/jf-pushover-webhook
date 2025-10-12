from importlib import reload
from unittest.mock import MagicMock, patch

import pytest
import requests


# Block any accidental real network
@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):  # pragma: no cover
        raise AssertionError("Network access is disabled in tests")

    monkeypatch.setattr("requests.sessions.Session.request", _blocked)


# Ensure Pushover tokens are always present unless a test deletes them explicitly
@pytest.fixture(autouse=True)
def pushover_env(monkeypatch):
    monkeypatch.setenv("PUSHOVER_API_TOKEN", "pushover-token")
    monkeypatch.setenv("PUSHOVER_USER_KEY", "pushover-user")


@pytest.fixture
def client():
    from app.app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _mock_image_get(return_bytes=b"img"):
    r = MagicMock()
    r.iter_content.return_value = [return_bytes]
    r.raise_for_status = MagicMock()
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


# Index and health


def test_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["service"] == "jf-pushover-webhook"
    assert "/jf-pushover-webhook" in data["endpoints"]


def test_health_healthy(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "healthy"


def test_health_degraded_when_missing_tokens(monkeypatch):
    monkeypatch.delenv("PUSHOVER_API_TOKEN", raising=False)
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        resp = c.get("/health")
        assert resp.status_code == 500
        assert resp.get_json()["status"] == "degraded"


# Flexible /webhook


def test_webhook_get(client):
    r = client.get("/webhook")
    assert r.status_code == 200
    assert "Use POST" in r.get_json()["message"]


def test_webhook_post_json_no_auth_required(client):
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


def test_webhook_unsupported_media_type(client):
    r = client.post("/webhook", data="<xml/>", content_type="application/xml")
    assert r.status_code == 415
    assert r.get_json()["error"] == "Unsupported Media Type"


def test_webhook_missing_message(client):
    r = client.post("/webhook", json={"title": "no body"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "Missing 'message'"


def test_webhook_with_remote_image_and_json_response(client):
    with (
        patch("app.app.session.get", return_value=_mock_image_get(b"abc")) as mock_get,
        patch("app.app.session.post", return_value=_mock_pushover_post(json_body={"status": 1})) as mock_post,
    ):
        r = client.post("/webhook", json={"message": "img", "image_url": "http://example/img.jpg"})
        assert r.status_code == 200
        mock_get.assert_called_once_with("http://example/img.jpg", timeout=10, stream=True)
        body = r.get_json()["pushover_response"]
        assert body["status"] == 1
        assert mock_post.called


def test_webhook_image_download_http_error_returns_502(client):
    http_err = requests.exceptions.HTTPError()
    http_err.response = MagicMock(status_code=404)
    with patch("app.app.session.get", side_effect=http_err):
        r = client.post("/webhook", json={"message": "x", "image_url": "http://bad/img.jpg"})
        assert r.status_code == 502
        assert "Failed to send Pushover notification" in r.get_json()["error"]


def test_webhook_tokens_missing_returns_500(monkeypatch):
    # Delete tokens to trigger the RuntimeError branch inside _send_pushover
    monkeypatch.delenv("PUSHOVER_API_TOKEN", raising=False)
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c, patch("app.app.session.post", return_value=_mock_pushover_post("ok")):
        r = c.post("/webhook", json={"message": "x"})
        assert r.status_code == 500
        assert (
            "Failed to send Pushover notification" in r.get_json()["error"] or "Internal error" in r.get_json()["error"]
        )


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


def test_jf_with_header_base_url_success(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    with (
        app_module.app.test_client() as c,
        patch("app.app.session.get", return_value=_mock_image_get(b"xyz")) as mock_get,
        patch("app.app.session.post", return_value=_mock_pushover_post("ok")) as mock_post,
    ):
        r = c.post(
            "/jf-pushover-webhook",
            headers={"Authorization": "Bearer secret", "X-Jellyfin-URL": "http://jf.local/"},
            json=_jf_payload(),
        )
        assert r.status_code == 200
        mock_get.assert_called_once_with("http://jf.local/Items/123/Images/Primary", timeout=10, stream=True)
        _, kwargs = mock_post.call_args
        assert kwargs["data"]["title"].startswith("PlaybackStart - Great Show")


def test_jf_title_without_series_uses_itemtype(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
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
            headers={"Authorization": "Bearer secret", "X-Jellyfin-URL": "http://jf.local"},
            json=_jf_payload(SeriesName="", ItemType="Movie", ItemName="Something"),
        )
        assert r.status_code == 200
        _, kwargs = mock_post.call_args
        assert kwargs["data"]["title"] == "PlaybackStart - Movie: Something"


def test_jf_payload_base_url_resolution(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
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
            json=_jf_payload(ServerUrl="https://jf.payload/"),
        )
        assert r.status_code == 200
        assert mock_post.called


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
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        payload = _jf_payload()
        payload.pop("ItemId")
        r = c.post("/jf-pushover-webhook", headers={"Authorization": "Bearer secret"}, json=payload)
        assert r.status_code == 400
        assert r.get_json()["error"] == "Missing ItemId in payload"


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


def test_jf_image_download_connection_error_returns_500(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret")
    monkeypatch.setenv("JELLYFIN_BASE_URL", "http://jf.example")
    import app.app as app_module

    reload(app_module)
    app_module.app.config["TESTING"] = True

    conn_err = requests.exceptions.ConnectionError("down")
    with app_module.app.test_client() as c, patch("app.app.session.get", side_effect=conn_err):
        r = c.post("/jf-pushover-webhook", headers={"Authorization": "Bearer secret"}, json=_jf_payload())
        assert r.status_code == 500
        assert "Failed to send Pushover notification" in r.get_json()["error"]
