import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import core.secret_vault as secret_vault
from main import app
from routers import projects


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
                "role": "admin" if user_id == "tenant-owner" else "reviewer",
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


def test_strict_tenant_isolation_blocks_cross_tenant_decision_artifact_routes(monkeypatch):
    client = TestClient(app)
    for user_id, tenant_id in (
        ("decision-artifact-owner", "decision-artifact-tenant-one"),
        ("decision-artifact-outsider", "decision-artifact-tenant-two"),
    ):
        client.post("/auth/tenants", json={"tenantId": tenant_id, "name": tenant_id})
        client.post(
            "/auth/users",
            json={
                "userId": user_id,
                "tenantId": tenant_id,
                "displayName": user_id,
                "role": "admin",
            },
        )

    owner_session = client.post(
        "/auth/session",
        json={
            "userId": "decision-artifact-owner",
            "tenantId": "decision-artifact-tenant-one",
            "provider": "local-test",
        },
    ).json()["session"]["sessionId"]
    outsider_session = client.post(
        "/auth/session",
        json={
            "userId": "decision-artifact-outsider",
            "tenantId": "decision-artifact-tenant-two",
            "provider": "local-test",
        },
    ).json()["session"]["sessionId"]
    project = client.post(
        "/projects/",
        headers={"x-realytics-session-id": owner_session},
        json={"project": {"name": "Decision Artifact Boundary", "type": "residential"}},
    ).json()
    project_id = project["project"]["id"]
    outsider_headers = {"x-realytics-session-id": outsider_session}
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")

    responses = [
        client.get(f"/projects/{project_id}/orchestrations", headers=outsider_headers),
        client.get(
            f"/projects/{project_id}/orchestrations/missing-run",
            headers=outsider_headers,
        ),
        client.get(
            f"/projects/{project_id}/orchestrations/missing-run/credit-documents",
            headers=outsider_headers,
        ),
        client.get(
            f"/projects/{project_id}/orchestrations/missing-run/credit-documents/term-sheet/export",
            headers=outsider_headers,
        ),
        client.get(f"/projects/{project_id}/risk-registers", headers=outsider_headers),
        client.get(
            f"/projects/{project_id}/orchestrations/missing-run/risk-register",
            headers=outsider_headers,
        ),
        client.get(f"/projects/{project_id}/evidence-graphs", headers=outsider_headers),
        client.get(
            f"/projects/{project_id}/orchestrations/missing-run/evidence-graph",
            headers=outsider_headers,
        ),
        client.post(
            f"/projects/{project_id}/orchestrations/missing-run/approve",
            headers=outsider_headers,
            json={},
        ),
        client.post(
            f"/projects/{project_id}/orchestrations/missing-run/handoffs/missing-handoff/review",
            headers=outsider_headers,
            json={},
        ),
        client.post(
            f"/projects/{project_id}/orchestrations/missing-run/risk-register/missing-risk/review",
            headers=outsider_headers,
            json={},
        ),
        client.get(f"/projects/{project_id}/source-documents", headers=outsider_headers),
        client.get(
            f"/projects/{project_id}/source-documents/missing-archive/download",
            headers=outsider_headers,
        ),
        client.get(
            f"/projects/{project_id}/source-documents/missing-archive/content",
            headers=outsider_headers,
        ),
        client.get(
            f"/projects/{project_id}/source-documents/missing-archive/pdf/pages/1.png",
            headers=outsider_headers,
        ),
        client.get(
            f"/projects/{project_id}/source-documents/missing-archive/workbook/cells",
            headers=outsider_headers,
            params={"sheet_name": "Sheet1", "cell_reference": "A1"},
        ),
        client.post(
            f"/projects/{project_id}/reports/generate",
            headers=outsider_headers,
            json={},
        ),
        client.get(f"/projects/{project_id}/reports", headers=outsider_headers),
        client.get(
            f"/projects/{project_id}/reports/missing-report",
            headers=outsider_headers,
        ),
        client.post(
            f"/projects/{project_id}/reports/missing-report/exports",
            headers=outsider_headers,
            json={"format": "markdown"},
        ),
        client.get(
            f"/projects/{project_id}/reports/missing-report/exports",
            headers=outsider_headers,
        ),
        client.get(
            f"/projects/{project_id}/reports/missing-report/exports/missing-export/download",
            headers=outsider_headers,
        ),
        client.post(
            f"/projects/{project_id}/reports/missing-report/publish",
            headers=outsider_headers,
        ),
        client.get(f"/projects/{project_id}/report-templates", headers=outsider_headers),
        client.get(
            f"/projects/{project_id}/report-templates/missing-template",
            headers=outsider_headers,
        ),
        client.post(
            f"/projects/{project_id}/report-templates",
            headers=outsider_headers,
            json={},
        ),
        client.get(f"/projects/{project_id}/snapshots", headers=outsider_headers),
        client.get(
            f"/projects/{project_id}/snapshots/missing-snapshot",
            headers=outsider_headers,
        ),
        client.get(f"/projects/{project_id}/similar-cases/rag", headers=outsider_headers),
        client.post(
            f"/projects/{project_id}/snapshots/similar",
            headers=outsider_headers,
            json={},
        ),
        client.post(
            f"/projects/{project_id}/snapshots/diff",
            headers=outsider_headers,
            json={},
        ),
        client.post(
            f"/projects/{project_id}/snapshots/missing-snapshot/outcome",
            headers=outsider_headers,
            json={},
        ),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "tenant_scope_mismatch"


def test_similar_case_snapshot_corpus_excludes_other_tenants_and_unknown_lineage():
    project_a = "snapshot-corpus-project-a"
    project_a_peer = "snapshot-corpus-project-a-peer"
    project_b = "snapshot-corpus-project-b"
    projects.PROJECTS_DB[project_a] = {"_meta": {"tenantId": "snapshot-tenant-a"}}
    projects.PROJECTS_DB[project_a_peer] = {"_meta": {"tenantId": "snapshot-tenant-a"}}
    projects.PROJECTS_DB[project_b] = {"_meta": {"tenantId": "snapshot-tenant-b"}}
    snapshots = [
        {"snapshotId": "same-project", "projectId": project_a},
        {"snapshotId": "same-tenant", "projectId": project_a_peer},
        {"snapshotId": "other-tenant", "projectId": project_b},
        {"snapshotId": "unknown-lineage", "projectId": "missing-project"},
    ]

    try:
        scoped = projects._tenant_scoped_snapshot_corpus(project_a, snapshots)
    finally:
        projects.PROJECTS_DB.pop(project_a, None)
        projects.PROJECTS_DB.pop(project_a_peer, None)
        projects.PROJECTS_DB.pop(project_b, None)

    assert {item["snapshotId"] for item in scoped} == {"same-project", "same-tenant"}


def test_auth_me_returns_anonymous_context_when_user_missing():
    client = TestClient(app)

    me = client.get("/auth/me", headers={"x-realytics-user-id": "missing-user"})

    assert me.status_code == 200
    assert me.json()["authenticated"] is False
    assert me.json()["user"]["role"] == "anonymous"


def test_strict_rbac_blocks_anonymous_project_listing(monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    client = TestClient(app)

    response = client.get("/projects/")

    assert response.status_code == 403
    assert response.json()["detail"]["requiredPermission"] == "project:read"


def test_strict_rbac_blocks_viewer_report_export(monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")
    client = TestClient(app)

    admin_session = client.post(
        "/auth/session",
        json={
            "userId": "admin-user",
            "tenantId": "tenant-auth",
            "role": "admin",
            "provider": "local-test",
        },
    ).json()["session"]["sessionId"]
    created = client.post(
        "/projects/",
        headers={"x-realytics-session-id": admin_session},
        json={"project": {"name": "Strict report export", "type": "mixed_use"}},
    )
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]

    reviewer_session = client.post(
        "/auth/session",
        json={
            "userId": "reviewer-user",
            "tenantId": "tenant-auth",
            "role": "reviewer",
            "provider": "local-test",
        },
    ).json()["session"]["sessionId"]
    report = client.post(
        f"/projects/{project_id}/reports/generate",
        headers={"x-realytics-session-id": reviewer_session},
        json={},
    )
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

    allowed = client.post(
        f"/projects/{project_id}/reports/{report_id}/exports",
        headers={"x-realytics-session-id": reviewer_session},
        json={"format": "markdown"},
    )
    assert allowed.status_code == 200


def test_reviewer_can_run_analysis_but_does_not_get_project_write(monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    client = TestClient(app)
    admin_session = client.post(
        "/auth/session",
        json={
            "userId": "analysis-admin-user",
            "tenantId": "tenant-auth",
            "role": "admin",
            "provider": "local-test",
        },
    ).json()["session"]["sessionId"]
    created = client.post(
        "/projects/",
        headers={"x-realytics-session-id": admin_session},
        json={
            "project": {"name": "Reviewer Analysis Contract", "type": "mixed_use"},
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
    projects.source_document_repository().save_upload_metadata(
        project_id,
        {
            "documentType": "business_plan",
            "originalFileName": "reviewer-analysis-fixture.json",
            "archiveId": "reviewer-analysis-fixture",
        },
    )
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
            "realytics_principal_type": "human",
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
    assert body["user"]["metadata"]["principalType"] == "human"
    assert "risk:review" in body["permissions"]
    assert body["identityProvider"]["mode"] == "hs256"
    assert "hs256SecretConfigured" not in body["identityProvider"]


def test_client_credentials_bearer_is_not_classified_as_human(monkeypatch):
    monkeypatch.setenv("REALYTICS_IDP_MODE", "hs256")
    monkeypatch.setenv("REALYTICS_IDP_JWT_SECRET", "test-idp-secret")
    client = TestClient(app)
    token = _make_hs256_token(
        {
            "sub": "parser-service",
            "tenant_id": "tenant-idp",
            "role": "reviewer",
            "gty": "client_credentials",
            "exp": int(time.time()) + 3600,
        },
        "test-idp-secret",
    )

    me = client.get("/auth/me", headers={"authorization": f"Bearer {token}"})

    assert me.status_code == 200
    assert me.json()["user"]["metadata"]["principalType"] == "service"


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


def test_supabase_bearer_byok_uses_signed_session_id_and_rejects_other_session(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "bearer-byok.sqlite3"))
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "session_memory")
    monkeypatch.setenv("REALYTICS_AI_CREDENTIAL_TTL_SECONDS", "3600")
    monkeypatch.setenv("REALYTICS_IDP_MODE", "hs256")
    monkeypatch.setenv("REALYTICS_IDP_JWT_SECRET", "bearer-byok-secret")
    monkeypatch.setenv("REALYTICS_IDP_PROVIDER", "supabase")
    monkeypatch.setenv("REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY", "claims")
    client = TestClient(app)
    base_claims = {
        "iss": "https://pilot.supabase.co/auth/v1",
        "sub": "supabase-byok-user",
        "realytics_tenant_id": "supabase-byok-tenant",
        "realytics_role": "reviewer",
        "exp": int(time.time()) + 3600,
    }
    token_a = _make_hs256_token(
        {**base_claims, "session_id": "supabase-provider-session-a"},
        "bearer-byok-secret",
    )
    token_b = _make_hs256_token(
        {**base_claims, "session_id": "supabase-provider-session-b"},
        "bearer-byok-secret",
    )
    headers_a = {"authorization": f"Bearer {token_a}"}
    headers_b = {"authorization": f"Bearer {token_b}"}

    ingested = client.post(
        "/auth/ai-credentials",
        headers=headers_a,
        json={
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "apiKey": "supabase-session-secret-value",
        },
    )
    assert ingested.status_code == 200
    assert "supabase-session-secret-value" not in json.dumps(ingested.json())
    assert "supabase-provider-session-a" not in json.dumps(ingested.json())
    assert (
        client.get("/auth/ai-profile", headers=headers_a).json()["effectiveAi"][
            "selectionSource"
        ]
        == "user_ai_profile"
    )
    assert (
        client.get("/auth/ai-profile", headers=headers_b).json()["effectiveAi"][
            "selectionSource"
        ]
        == "user_ai_profile_without_key_system_fallback"
    )
    monkeypatch.setitem(
        projects.PROJECTS_DB,
        "supabase-byok-project",
        {"id": "supabase-byok-project", "name": "Supabase BYOK Project"},
    )
    owner_project_ai = client.post(
        "/projects/supabase-byok-project/ai/connection/resolve",
        headers=headers_a,
        json={},
    )
    other_project_ai = client.post(
        "/projects/supabase-byok-project/ai/connection/resolve",
        headers=headers_b,
        json={},
    )
    assert owner_project_ai.status_code == 200
    assert (
        owner_project_ai.json()["ai_connection"]["selectionSource"]
        == "user_ai_profile"
    )
    assert other_project_ai.status_code == 200
    assert (
        other_project_ai.json()["ai_connection"]["selectionSource"]
        == "user_ai_profile_without_key_system_fallback"
    )

    wrong_session_revoke = client.delete(
        "/auth/ai-credentials/current",
        headers=headers_b,
    )
    assert wrong_session_revoke.status_code == 409
    assert wrong_session_revoke.json()["detail"]["error"] == "credential_session_mismatch"
    assert (
        client.get("/auth/ai-profile", headers=headers_a).json()["effectiveAi"][
            "selectionSource"
        ]
        == "user_ai_profile"
    )

    owner_revoke = client.delete("/auth/ai-credentials/current", headers=headers_a)
    assert owner_revoke.status_code == 200
    assert owner_revoke.json()["credentialRevoked"] is True


def test_bearer_byok_fails_closed_without_signed_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "bearer-without-session.sqlite3"))
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "session_memory")
    monkeypatch.setenv("REALYTICS_IDP_MODE", "hs256")
    monkeypatch.setenv("REALYTICS_IDP_JWT_SECRET", "bearer-no-session-secret")
    monkeypatch.setenv("REALYTICS_IDP_PROVIDER", "supabase")
    monkeypatch.setenv("REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY", "claims")
    token = _make_hs256_token(
        {
            "iss": "https://pilot.supabase.co/auth/v1",
            "sub": "supabase-no-session-user",
            "realytics_tenant_id": "supabase-no-session-tenant",
            "realytics_role": "reviewer",
            "exp": int(time.time()) + 3600,
        },
        "bearer-no-session-secret",
    )

    response = TestClient(app).post(
        "/auth/ai-credentials",
        headers={"authorization": f"Bearer {token}"},
        json={"provider": "gemini", "apiKey": "must-not-be-stored"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "credential_session_binding_required"


def test_expired_byok_reference_is_cleared_from_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "expired-byok.sqlite3"))
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "session_memory")
    monkeypatch.setenv("REALYTICS_AI_CREDENTIAL_TTL_SECONDS", "60")
    now = datetime(2026, 7, 28, 2, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(secret_vault, "_utc_now", lambda: now)
    client = TestClient(app)
    client.post("/auth/tenants", json={"tenantId": "expired-tenant", "name": "Expired Tenant"})
    client.post(
        "/auth/users",
        json={
            "userId": "expired-user",
            "tenantId": "expired-tenant",
            "role": "reviewer",
        },
    )
    session_id = client.post(
        "/auth/session",
        json={"userId": "expired-user", "tenantId": "expired-tenant"},
    ).json()["session"]["sessionId"]
    headers = {"x-realytics-session-id": session_id}
    assert client.post(
        "/auth/ai-credentials",
        headers=headers,
        json={"provider": "gemini", "apiKey": "short-lived-secret"},
    ).status_code == 200

    monkeypatch.setattr(
        secret_vault,
        "_utc_now",
        lambda: now + timedelta(seconds=61),
    )
    profile = client.get("/auth/ai-profile", headers=headers)

    assert profile.status_code == 200
    assert profile.json()["profile"]["apiKeyConfigured"] is False
    assert profile.json()["profile"]["credentialRefConfigured"] is False
    assert (
        profile.json()["effectiveAi"]["selectionSource"]
        == "user_ai_profile_without_key_system_fallback"
    )


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
