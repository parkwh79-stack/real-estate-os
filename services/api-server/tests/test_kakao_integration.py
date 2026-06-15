from fastapi.testclient import TestClient

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
