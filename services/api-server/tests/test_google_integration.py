from fastapi.testclient import TestClient
from urllib.parse import parse_qs, urlparse

from main import app
from routers import google as google_router


def test_google_status_is_secret_safe(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-client-secret-value")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://realytics.example/integrations/google/callback")
    client = TestClient(app)

    response = client.get("/integrations/google/status")

    assert response.status_code == 200
    body = response.json()
    assert body["login"]["provider"] == "google"
    assert body["login"]["clientIdConfigured"] is True
    assert body["login"]["clientSecretConfigured"] is True
    assert "google-client-secret-value" not in str(body)


def test_google_authorize_url_falls_back_to_pilot_mock(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    client = TestClient(app)

    response = client.post("/integrations/google/login/authorize-url", json={"redirectPath": "/dashboard"})

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["mode"] == "pilot_mock"
    assert "/integrations/google/callback" in body["authorizeUrl"]


def test_google_authorize_url_builds_real_consent_url(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://realytics.example/integrations/google/callback")
    client = TestClient(app)

    response = client.post("/integrations/google/login/authorize-url", json={"redirectPath": "/dashboard"})

    body = response.json()
    assert body["ready"] is True
    assert body["mode"] == "oauth"
    parsed = urlparse(body["authorizeUrl"])
    assert parsed.netloc == "accounts.google.com"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["client-id.apps.googleusercontent.com"]
    assert query["response_type"] == ["code"]
    assert "openid" in query["scope"][0]


def test_google_callback_pilot_mock_creates_session(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("REALYTICS_WEB_BASE_URL", "http://localhost:3000")
    client = TestClient(app)

    response = client.get(
        "/integrations/google/callback",
        params={"redirect_path": "/dashboard"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("http://localhost:3000/auth/kakao/session")
    assert "session_id=" in location


def test_google_callback_real_flow_exchanges_code(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://realytics.example/integrations/google/callback")
    monkeypatch.setenv("REALYTICS_WEB_BASE_URL", "http://localhost:3000")

    # The router imports these names directly, so patch them on the router module.
    monkeypatch.setattr(
        google_router,
        "exchange_google_authorization_code",
        lambda code, redirect_uri=None: {"access_token": "tok", "token_type": "Bearer", "scope": "openid email"},
    )
    monkeypatch.setattr(
        google_router,
        "fetch_google_user_info",
        lambda access_token: {
            "sub": "115551234567890",
            "email": "reviewer@example.com",
            "email_verified": True,
            "name": "심사역 구글",
        },
    )
    client = TestClient(app)

    response = client.get(
        "/integrations/google/callback",
        params={"code": "auth-code", "state": "%2Fdashboard"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("http://localhost:3000/auth/kakao/session")
    assert "session_id=" in location

    me = client.get(
        "/auth/me",
        headers={"X-REALYTICS-SESSION-ID": _session_id_from(location)},
    )
    body = me.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "reviewer@example.com"
    assert body["user"]["metadata"]["provider"] == "google"


def _session_id_from(location: str) -> str:
    return parse_qs(urlparse(location).query)["session_id"][0]
