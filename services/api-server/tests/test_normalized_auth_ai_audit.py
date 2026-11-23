"""Personal AI audit uses normalized append-only storage, never app_state DDL."""
from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

import storage
from routers import auth
from db.repositories.activity_log_repository import PostgresActivityLogRepository


@pytest.fixture
def normalized_auth_ai(monkeypatch):
    state = SimpleNamespace(user="verified-user", tenant="verified-tenant", rows={}, sql=[], fail_audit=False)
    monkeypatch.setattr(auth, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(auth, "identity_context_from_request", lambda _request: {
        "authenticated": True, "user": {"userId": state.user, "tenantId": state.tenant}})
    monkeypatch.setattr(auth, "credential_session_binding_from_request", lambda *_a: {
        "userId": state.user, "tenantId": state.tenant, "sessionId": "verified-session"})
    def forbidden(*_args, **_kwargs):
        pytest.fail("Normalized AI audit must not open legacy state or bootstrap DDL")
    monkeypatch.setattr(storage, "_load_json_state", forbidden)
    monkeypatch.setattr(auth, "record_user_activity_log", forbidden)
    def get(user, *, tenant_id, include_secret=False):
        return deepcopy(state.rows.get((tenant_id, user)))
    def save(record):
        assert (record["tenantId"], record["userId"]) == (state.tenant, state.user)
        profile = {key: record.get(key) for key in ("userId", "tenantId", "provider", "model", "status", "allowSystemFallback")}
        profile["apiKeyConfigured"] = False
        state.rows[(state.tenant, state.user)] = profile
        return deepcopy(profile)
    monkeypatch.setattr(auth, "_get_user_ai_profile", get)
    monkeypatch.setattr(auth, "_upsert_user_ai_profile", save)
    monkeypatch.setattr(auth, "_delete_user_ai_profile", lambda user, *, tenant_id: state.rows.pop((tenant_id, user), None) is not None)
    monkeypatch.setattr(auth, "build_user_ai_context", lambda **_kw: {"provider": "openai", "model": "fixture-model"})
    class Connection:
        def execute(self, sql, params):
            if state.fail_audit:
                raise PermissionError("sensitive SQL fixture detail")
            state.sql.append((sql, params))
            return SimpleNamespace(fetchone=lambda: (params[0],))
    @contextmanager
    def transaction(tenant):
        assert tenant == state.tenant
        yield Connection()
    monkeypatch.setattr(auth, "PostgresActivityLogRepository", lambda **scope:
                        PostgresActivityLogRepository(**scope, transaction_factory=transaction))
    app = FastAPI()
    app.include_router(auth.router)
    return state, TestClient(app)


def test_normalized_profile_save_update_delete_each_appends_one_scoped_event(normalized_auth_ai):
    state, client = normalized_auth_ai
    for model in ("fixture-a", "fixture-b"):
        response = client.post("/auth/ai-profile", json={"provider": "openai", "model": model, "status": "active",
                              "allowSystemFallback": False, "userId": "forged-user", "tenantId": "forged-tenant"})
        assert response.status_code == 200, response.text
        assert client.get("/auth/ai-profile").json()["profile"]["model"] == model
    assert client.delete("/auth/ai-profile").status_code == 200
    assert client.get("/auth/ai-profile").json()["profile"] is None
    assert [params[1] for _, params in state.sql] == ["ai_profile_updated", "ai_profile_updated", "ai_profile_deleted"]
    for sql, params in state.sql:
        assert "INSERT INTO realytics.user_activity_logs" in sql and "statement_timestamp()" in sql
        assert "CREATE" not in sql and "app_state" not in sql
        assert params[2:4] == ("verified-user", None) and params[-1] == "verified-tenant"
        assert not {"forged-user", "forged-tenant"}.intersection(params)
        assert json.loads(params[-2])["tenantId"] == "verified-tenant"


@pytest.mark.parametrize("action", ["save", "delete"])
def test_audit_failure_is_explicit_about_already_applied_mutation_without_retry(normalized_auth_ai, action):
    state, client = normalized_auth_ai
    if action == "delete":
        state.rows[(state.tenant, state.user)] = {"userId": state.user, "tenantId": state.tenant, "provider": "openai", "status": "active"}
    state.fail_audit = True
    response = (client.post("/auth/ai-profile", json={"provider": "openai", "model": "fixture-model", "status": "active"})
                if action == "save" else client.delete("/auth/ai-profile"))
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "ai_profile_audit_unavailable"
    assert detail["mutationState"] == "may_have_applied" and detail["retryable"] is False
    assert "현재 설정을 조회" in detail["message"] and "자동 재시도하지" in detail["message"]
    assert "sensitive SQL" not in response.text and state.sql == []
    profile = client.get("/auth/ai-profile").json()["profile"]
    assert (profile is not None) is (action == "save")


@pytest.mark.parametrize("event_type", ["ai_credential_ingested", "ai_profile_updated", "ai_profile_deleted", "ai_credential_revoked", "logout"])
def test_ai_audit_drops_untrusted_identity_and_secret_metadata(normalized_auth_ai, event_type):
    state, _client = normalized_auth_ai
    request = Request({"type": "http", "method": "POST", "scheme": "http", "path": "/auth/ai-profile", "headers": []})
    auth._record_ai_profile_activity(request, {"authenticated": True, "user": {"userId": state.user, "tenantId": state.tenant}}, {
        "eventType": event_type, "userId": "forged-user", "projectId": "forged-project",
        "metadata": {"tenantId": "forged-tenant", "apiKey": "secret-fixture", "credentialRef": "secret-reference",
                     "sessionId": "secret-session", "provider": "openai", "allowSystemFallback": False},
    })
    _, params = state.sql[0]
    assert params[1:4] == (event_type, "verified-user", None)
    assert json.loads(params[-2]) == {"tenantId": "verified-tenant", "provider": "openai", "allowSystemFallback": False}
    assert "secret-" not in str(params) and "forged-" not in str(params)


@pytest.mark.parametrize("context", [{"authenticated": False, "user": {"userId": "u", "tenantId": "t"}},
    {"authenticated": True, "user": {"userId": "u"}}, {"authenticated": True, "user": {"tenantId": "t"}}])
def test_ai_audit_requires_verified_user_and_tenant_before_sql(normalized_auth_ai, context):
    state, _client = normalized_auth_ai
    request = Request({"type": "http", "method": "POST", "path": "/auth/ai-profile", "headers": []})
    with pytest.raises(HTTPException) as caught:
        auth._record_ai_profile_activity(request, context, {"eventType": "ai_profile_updated"})
    assert caught.value.status_code == 401 and state.sql == []


def test_ai_audit_keeps_local_compatibility_without_normalized_pool(monkeypatch):
    captured = []
    monkeypatch.setattr(auth, "storage_backend", lambda: "sqlite")
    monkeypatch.setattr(auth, "record_user_activity_log", captured.append)
    monkeypatch.setattr(auth, "PostgresActivityLogRepository", lambda **_kw: pytest.fail("Local audit must not acquire a PostgreSQL pool"))
    event = {"eventType": "ai_profile_updated", "userId": "local-user", "metadata": {"provider": "openai"}}
    auth._record_ai_profile_activity(Request({"type": "http", "method": "POST", "path": "/auth/ai-profile", "headers": []}), {}, event)
    assert captured == [event]
