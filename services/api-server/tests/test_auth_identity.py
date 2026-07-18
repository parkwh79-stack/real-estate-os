import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from main import app


def test_auth_identity_session_and_me_context():
    client = TestClient(app)

    tenant = client.post(
        "/auth/tenants",
        json={"tenantId": "tenant-auth", "name": "Auth Tenant"},
    )
    assert tenant.status_code == 200
    assert tenant.json()["tenant"]["tenantId"] == "tenant-auth"

    user = client.post(
        "/auth/users",
        json={
            "userId": "auth-user",
            "tenantId": "tenant-auth",
            "email": "auth@example.com",
            "displayName": "Auth User",
            "role": "reviewer",
        },
    )
    assert user.status_code == 200
    assert user.json()["user"]["role"] == "reviewer"

    session = client.post(
        "/auth/session",
        json={
            "userId": "auth-user",
            "tenantId": "tenant-auth",
            "provider": "local-test",
            "host": "web",
        },
    )
    assert session.status_code == 200
    session_id = session.json()["session"]["sessionId"]

    me = client.get("/auth/me", headers={"x-realytics-session-id": session_id})
    assert me.status_code == 200
    body = me.json()
    assert body["authenticated"] is True
    assert body["user"]["userId"] == "auth-user"
    assert "risk:review" in body["permissions"]

    projects = client.get("/projects/", headers={"x-realytics-session-id": session_id})
    assert projects.status_code == 200

    logs = client.get("/audit/user-activity", params={"user_id": "auth-user", "limit": 20}).json()["logs"]
    assert any(record["eventType"] == "login" for record in logs)
    assert any(record.get("path") == "/projects/" for record in logs)


def test_project_list_scopes_to_authenticated_session_owner():
    client = TestClient(app)

    for user_id in ["owner-a", "owner-b"]:
        tenant = f"{user_id}-tenant"
        client.post("/auth/tenants", json={"tenantId": tenant, "name": tenant})
        client.post(
            "/auth/users",
            json={
                "userId": user_id,
                "tenantId": tenant,
                "displayName": user_id,
                "role": "analyst",
            },
        )

    session_a = client.post(
        "/auth/session",
        json={"userId": "owner-a", "tenantId": "owner-a-tenant", "provider": "local-test", "host": "web"},
    ).json()["session"]["sessionId"]
    session_b = client.post(
        "/auth/session",
        json={"userId": "owner-b", "tenantId": "owner-b-tenant", "provider": "local-test", "host": "web"},
    ).json()["session"]["sessionId"]

    project_a = client.post(
        "/projects/",
        headers={"x-realytics-session-id": session_a},
        json={"project": {"name": "Owner A Project", "type": "mixed_use"}},
    )
    assert project_a.status_code == 200
    assert project_a.json()["_meta"]["createdBy"] == "owner-a"

    project_b = client.post(
        "/projects/",
        headers={"x-realytics-session-id": session_b},
        json={"project": {"name": "Owner B Project", "type": "residential"}},
    )
    assert project_b.status_code == 200

    projects_for_a = client.get("/projects/", headers={"x-realytics-session-id": session_a})
    assert projects_for_a.status_code == 200
    names_for_a = {item["project"]["name"] for item in projects_for_a.json()}
    assert "Owner A Project" in names_for_a
    assert "Owner B Project" not in names_for_a


def test_strict_tenant_isolation_blocks_cross_tenant_project_access(monkeypatch):
    client = TestClient(app)
    for user_id, tenant_id in (("tenant-owner", "tenant-one"), ("tenant-outsider", "tenant-two")):
        client.post("/auth/tenants", json={"tenantId": tenant_id, "name": tenant_id})
        client.post(
            "/auth/users",
            json={
                "userId": user_id,
                "tenantId": tenant_id,
                "displayName": user_id,
                "role": "reviewer",
            },
        )
    owner_session = client.post(
        "/auth/session",
        json={"userId": "tenant-owner", "tenantId": "tenant-one", "provider": "local-test"},
    ).json()["session"]["sessionId"]
    outsider_session = client.post(
        "/auth/session",
        json={"userId": "tenant-outsider", "tenantId": "tenant-two", "provider": "local-test"},
    ).json()["session"]["sessionId"]
    project = client.post(
        "/projects/",
        headers={"x-realytics-session-id": owner_session},
        json={"project": {"name": "Tenant Isolated Project", "type": "residential"}},
    ).json()
    project_id = project["project"]["id"]
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")

    allowed = client.get(
        f"/projects/{project_id}",
        headers={"x-realytics-session-id": owner_session},
    )
    blocked = client.get(
        f"/projects/{project_id}",
        headers={"x-realytics-session-id": outsider_session},
    )

    assert allowed.status_code == 200
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["error"] == "tenant_scope_mismatch"


def test_auth_me_returns_anonymous_context_when_user_missing():
    client = TestClient(app)

    me = client.get("/auth/me", headers={"x-realytics-user-id": "missing-user"})

    assert me.status_code == 200
    assert me.json()["authenticated"] is False
    assert me.json()["user"]["role"] == "anonymous"


def test_strict_rbac_blocks_viewer_report_export(monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    client = TestClient(app)
    project_id = client.get("/projects/").json()[0]["project"]["id"]
    report = client.post(f"/projects/{project_id}/reports/generate", json={})
    assert report.status_code == 200
    report_id = report.json()["report_id"]

    viewer_session = client.post(
        "/auth/session",
        json={
            "userId": "viewer-user",
            "tenantId": "tenant-auth",
            "role": "viewer",
            "provider": "local-test",
        },
    ).json()["session"]["sessionId"]
    blocked = client.post(
        f"/projects/{project_id}/reports/{report_id}/exports",
        headers={"x-realytics-session-id": viewer_session},
        json={"format": "markdown"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["requiredPermission"] == "report:export"

    reviewer_session = client.post(
        "/auth/session",
        json={
            "userId": "reviewer-user",
            "tenantId": "tenant-auth",
            "role": "reviewer",
            "provider": "local-test",
        },
    ).json()["session"]["sessionId"]
    allowed = client.post(
        f"/projects/{project_id}/reports/{report_id}/exports",
        headers={"x-realytics-session-id": reviewer_session},
        json={"format": "markdown"},
    )
    assert allowed.status_code == 200


def test_reviewer_can_run_analysis_but_does_not_get_project_write(monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Reviewer Analysis Contract", "type": "mixed_use"},
            "source_documents": [
                {
                    "documentType": "business_plan",
                    "originalFileName": "reviewer-analysis-fixture.json",
                    "archiveId": "reviewer-analysis-fixture",
                }
            ],
            "land": {
                "pnu": "1111010100100010000",
                "area_sqm": 1000,
                "officialLandPricePerSqm": 1000000,
            },
            "regulation": {"zoning": "general_commercial"},
            "legal": {"permitStatus": "reviewed", "securityTrustStatus": "reviewed"},
            "landEvidence": {"officialLandPrice": {"pricePerSqm": 1000000}},
            "market": {"manualComparables": [{"id": "comp-1", "pricePerSqm": 10000000}]},
            "product_mix": [
                {
                    "type": "A",
                    "units": 10,
                    "avgUnitPrice": 100000000,
                    "totalRevenue": 1000000000,
                }
            ],
            "feasibility_input": {"total_revenue": 1000000000, "total_cost": 800000000},
            "schedule": {"months": 24},
            "pf_structure": [{"tranche": "Senior", "amount": 500000000, "priority": 1}],
        },
    )
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]
    session = client.post(
        "/auth/session",
        json={
            "userId": "analysis-reviewer-user",
            "tenantId": "tenant-auth",
            "role": "reviewer",
            "provider": "local-test",
        },
    ).json()["session"]["sessionId"]
    response = client.post(
        f"/projects/{project_id}/chief-orchestration/run-pod",
        headers={"x-realytics-session-id": session},
        json={
            "podKey": "land_legal",
            "options": {"useLiveConnectors": False, "autoEnrichLand": False},
        },
    )
    assert response.status_code == 200

    me = client.get("/auth/me", headers={"x-realytics-session-id": session}).json()
    assert "project:analyze" in me["permissions"]
    assert "project:write" not in me["permissions"]


def test_hs256_bearer_token_creates_idp_identity_context(monkeypatch):
    monkeypatch.setenv("REALYTICS_IDP_MODE", "hs256")
    monkeypatch.setenv("REALYTICS_IDP_JWT_SECRET", "test-idp-secret")
    monkeypatch.setenv("REALYTICS_IDP_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("REALYTICS_IDP_AUDIENCE", "realytics-api")
    client = TestClient(app)
    token = _make_hs256_token(
        {
            "iss": "https://idp.example.test",
            "aud": "realytics-api",
            "sub": "idp-user-1",
            "email": "idp-user@example.com",
            "name": "IdP Reviewer",
            "tenant_id": "tenant-idp",
            "role": "reviewer",
            "exp": int(time.time()) + 3600,
            "jti": "token-001",
        },
        "test-idp-secret",
    )

    me = client.get("/auth/me", headers={"authorization": f"Bearer {token}"})

    assert me.status_code == 200
    body = me.json()
    assert body["authenticated"] is True
    assert body["authSource"] == "bearer_token"
    assert body["user"]["userId"] == "idp-user@example.com"
    assert body["user"]["tenantId"] == "tenant-idp"
    assert body["user"]["role"] == "reviewer"
    assert "risk:review" in body["permissions"]
    assert body["identityProvider"]["mode"] == "hs256"
    assert "hs256SecretConfigured" not in body["identityProvider"]


def test_bearer_token_tenant_mismatch_is_blocked(monkeypatch):
    monkeypatch.setenv("REALYTICS_IDP_MODE", "hs256")
    monkeypatch.setenv("REALYTICS_IDP_JWT_SECRET", "test-idp-secret")
    client = TestClient(app)
    token = _make_hs256_token(
        {
            "sub": "tenant-user",
            "tenant_id": "tenant-a",
            "role": "reviewer",
            "exp": int(time.time()) + 3600,
        },
        "test-idp-secret",
    )

    me = client.get(
        "/auth/me",
        headers={
            "authorization": f"Bearer {token}",
            "x-realytics-tenant-id": "tenant-b",
        },
    )

    assert me.status_code == 403
    assert me.json()["detail"]["error"] == "tenant_mismatch"


def test_idp_status_does_not_return_secret(monkeypatch):
    monkeypatch.setenv("REALYTICS_IDP_MODE", "hs256")
    monkeypatch.setenv("REALYTICS_IDP_JWT_SECRET", "super-secret-value")
    monkeypatch.setenv("REALYTICS_IDP_PROVIDER", "okta")
    client = TestClient(app)

    status = client.get("/auth/idp/status")

    assert status.status_code == 200
    body = status.json()
    rendered = json.dumps(body)
    assert body["enabled"] is True
    assert body["provider"] == "okta"
    assert body["hs256SecretConfigured"] is True
    assert "super-secret-value" not in rendered


def test_byok_uses_dedicated_credential_ingest_and_is_revoked_on_logout(monkeypatch):
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "session_memory")
    client = TestClient(app)
    user_id = "byok-session-user"
    tenant_id = "byok-session-tenant"
    client.post("/auth/tenants", json={"tenantId": tenant_id, "name": "BYOK Tenant"})
    client.post(
        "/auth/users",
        json={
            "userId": user_id,
            "tenantId": tenant_id,
            "role": "reviewer",
            "displayName": "BYOK User",
        },
    )
    session_id = client.post(
        "/auth/session",
        json={"userId": user_id, "tenantId": tenant_id, "provider": "local-test"},
    ).json()["session"]["sessionId"]
    headers = {"x-realytics-session-id": session_id}

    rejected = client.post(
        "/auth/ai-profile",
        headers=headers,
        json={"provider": "gemini", "apiKey": "must-not-enter-profile"},
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["error"] == "secret_ingest_endpoint_required"

    ingested = client.post(
        "/auth/ai-credentials",
        headers=headers,
        json={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "apiKey": "test-secret-key-123456",
        },
    )
    assert ingested.status_code == 200
    body = ingested.json()
    rendered = json.dumps(body)
    assert body["profile"]["apiKeyConfigured"] is True
    assert body["profile"]["credentialRefConfigured"] is True
    assert "test-secret-key-123456" not in rendered
    assert "session-secret://" not in rendered

    revoked = client.delete("/auth/ai-credentials/current", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["credentialRevoked"] is True
    profile_after_revoke = client.get("/auth/ai-profile", headers=headers).json()["profile"]
    assert profile_after_revoke["apiKeyConfigured"] is False

    reingested = client.post(
        "/auth/ai-credentials",
        headers=headers,
        json={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "apiKey": "replacement-secret-key-654321",
        },
    )
    assert reingested.status_code == 200

    logout = client.delete("/auth/session/current", headers=headers)
    assert logout.status_code == 200
    assert logout.json()["sessionRevoked"] is True
    assert logout.json()["profileCredentialRevoked"] is True

    me_after_logout = client.get("/auth/me", headers=headers)
    assert me_after_logout.json()["authenticated"] is False


def _make_hs256_token(claims: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{encoded_header}.{encoded_payload}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64url(signature)}"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
