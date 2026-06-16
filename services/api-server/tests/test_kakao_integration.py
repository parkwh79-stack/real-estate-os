from fastapi.testclient import TestClient
from urllib.parse import parse_qs, urlparse
import base64
import json
import time

from integrations.kakao import KAKAO_ISSUER, create_kakao_oauth_state
from main import app


def test_kakao_status_is_secret_safe(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "rest-api-key-secret")
    monkeypatch.setenv("KAKAO_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("KAKAO_REDIRECT_URI", "https://realytics.example/auth/kakao/callback")
    client = TestClient(app)

    response = client.get("/integrations/kakao/status")

    assert response.status_code == 200
    body = response.json()
    assert body["login"]["provider"] == "kakao"
    assert body["login"]["restApiKeyConfigured"] is True
    assert body["login"]["clientSecretConfigured"] is True
    rendered = str(body)
    assert "rest-api-key-secret" not in rendered
    assert "client-secret-value" not in rendered


def test_kakao_authorize_url_reports_missing_config(monkeypatch):
    monkeypatch.delenv("KAKAO_REST_API_KEY", raising=False)
    monkeypatch.delenv("KAKAO_REDIRECT_URI", raising=False)
    client = TestClient(app)

    response = client.post("/integrations/kakao/login/authorize-url", json={"state": "s", "nonce": "n"})

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert "KAKAO_REST_API_KEY" in body["missingConfig"]
    assert "KAKAO_REDIRECT_URI" in body["missingConfig"]


def test_kakao_authorize_url_uses_oidc_scope(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "kakao-rest-key")
    monkeypatch.setenv("KAKAO_REDIRECT_URI", "https://realytics.example/auth/kakao/callback")
    client = TestClient(app)

    response = client.post("/integrations/kakao/login/authorize-url", json={"state": "state-1", "nonce": "nonce-1"})

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert "https://kauth.kakao.com/oauth/authorize" in body["authorizeUrl"]
    assert "client_id=kakao-rest-key" in body["authorizeUrl"]
    assert "scope=openid" in body["authorizeUrl"]
    assert "state=state-1" in body["authorizeUrl"]
    assert "nonce=nonce-1" in body["authorizeUrl"]


def test_kakao_message_preview_blocks_sensitive_pf_data():
    client = TestClient(app)

    response = client.post(
        "/integrations/kakao/messages/preview",
        json={
            "eventType": "legal_evidence_required",
            "projectId": "pilot",
            "projectName": "본리동 PF",
            "body": "법무 증빙 필요. PF 금액 90000000000원 계좌 123-456-789012",
            "actionUrl": "https://realytics.example/project/pilot/orchestration",
            "pfAmount": 90000000000,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["readyToSend"] is False
    assert "sensitive_field:pfAmount" in body["blockedReasons"]
    assert any(reason.startswith("sensitive_text:body") for reason in body["blockedReasons"])
    assert "90000000000" not in body["templateObject"]["text"]


def test_kakao_message_preview_allows_safe_event_summary():
    client = TestClient(app)

    response = client.post(
        "/integrations/kakao/messages/preview",
        json={
            "eventType": "risk_review_required",
            "projectId": "pilot",
            "projectName": "본리동 PF",
            "body": "본리동 PF: 리스크 검토 필요. REALYTICS에서 확인하세요.",
            "actionUrl": "https://realytics.example/project/pilot/orchestration",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["readyToSend"] is True
    assert body["templateObject"]["button_title"] == "REALYTICS에서 확인"


def test_kakao_message_preview_builds_mobile_action_url_from_project_id(monkeypatch):
    monkeypatch.setenv("REALYTICS_WEB_BASE_URL", "https://realytics.example")
    client = TestClient(app)

    response = client.post(
        "/integrations/kakao/messages/preview",
        json={
            "eventType": "approval_readiness_changed",
            "projectId": "pilot",
            "projectName": "본리동 PF",
            "body": "본리동 PF: 승인 가능성 상태가 변경되었습니다. REALYTICS에서 확인하세요.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["readyToSend"] is True
    assert body["recommendedMobilePath"] == "/project/pilot/mobile?source=kakao&event=approval_readiness_changed"
    assert body["templateObject"]["link"]["web_url"] == "https://realytics.example/project/pilot/mobile?source=kakao&event=approval_readiness_changed"


def test_kakao_authorize_url_generates_signed_state_when_omitted(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "kakao-rest-key")
    monkeypatch.setenv("KAKAO_REDIRECT_URI", "https://realytics.example/auth/kakao/callback")
    client = TestClient(app)

    response = client.post(
        "/integrations/kakao/login/authorize-url",
        json={"redirectPath": "/project/pilot/mobile?source=kakao"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["state"].startswith("rk1.")
    assert "state=rk1." in body["authorizeUrl"]


def test_kakao_callback_creates_realytics_session_and_redirects(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "kakao-rest-key")
    monkeypatch.setenv("KAKAO_REDIRECT_URI", "https://api.example/auth/kakao/callback")
    monkeypatch.setenv("REALYTICS_WEB_BASE_URL", "https://realytics.example")
    monkeypatch.setenv("KAKAO_DEFAULT_TENANT_ID", "tenant-kakao")
    monkeypatch.setenv("KAKAO_DEFAULT_ROLE", "reviewer")
    nonce = "nonce-callback"
    state = create_kakao_oauth_state(nonce=nonce, redirect_path="/project/pilot/mobile?source=kakao")
    id_token = _fake_jwt(
        {
            "iss": KAKAO_ISSUER,
            "aud": "kakao-rest-key",
            "sub": "12345",
            "email": "kakao.reviewer@example.com",
            "nickname": "카카오 심사역",
            "nonce": nonce,
            "exp": int(time.time()) + 600,
        }
    )

    monkeypatch.setattr(
        "routers.auth.exchange_kakao_authorization_code",
        lambda code, redirect_uri=None: {
            "access_token": "access-token",
            "id_token": id_token,
            "token_type": "bearer",
            "scope": "openid profile_nickname account_email",
        },
    )
    monkeypatch.setattr(
        "routers.auth.fetch_kakao_user_info",
        lambda access_token: {
            "id": 12345,
            "kakao_account": {
                "email": "kakao.reviewer@example.com",
                "profile": {"nickname": "카카오 심사역"},
            },
        },
    )
    client = TestClient(app)

    response = client.get(
        "/auth/kakao/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("https://realytics.example/auth/kakao/session")
    query = parse_qs(urlparse(location).query)
    session_id = query["session_id"][0]
    assert query["redirect"][0] == "/project/pilot/mobile?source=kakao"

    me = client.get("/auth/me", headers={"x-realytics-session-id": session_id})
    assert me.status_code == 200
    body = me.json()
    assert body["authenticated"] is True
    assert body["user"]["userId"] == "kakao.reviewer@example.com"
    assert body["user"]["tenantId"] == "tenant-kakao"
    assert body["session"]["provider"] == "kakao"

    logs = client.get("/audit/user-activity", params={"user_id": "kakao.reviewer@example.com", "limit": 20}).json()["logs"]
    assert any(log["eventType"] == "kakao_login_callback" for log in logs)


def test_kakao_message_preview_records_audit_log(monkeypatch):
    monkeypatch.setenv("REALYTICS_WEB_BASE_URL", "https://realytics.example")
    client = TestClient(app)

    response = client.post(
        "/integrations/kakao/messages/preview",
        headers={"x-realytics-user-id": "kakao-auditor"},
        json={
            "eventType": "risk_review_required",
            "projectId": "pilot",
            "body": "리스크 검토 필요. REALYTICS에서 확인하세요.",
        },
    )

    assert response.status_code == 200
    logs = client.get("/audit/user-activity", params={"user_id": "kakao-auditor", "limit": 20}).json()["logs"]
    preview_logs = [log for log in logs if log["eventType"] == "kakao_message_preview"]
    assert preview_logs
    assert preview_logs[-1]["projectId"] == "pilot"
    assert preview_logs[-1]["metadata"]["recommendedMobilePath"] == "/project/pilot/mobile?source=kakao&event=risk_review_required"


def _fake_jwt(payload: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}
    return f"{_b64url(header)}.{_b64url(payload)}.sig"


def _b64url(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
