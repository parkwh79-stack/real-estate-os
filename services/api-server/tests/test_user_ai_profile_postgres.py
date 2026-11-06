from contextlib import contextmanager
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import storage
from db.repositories.user_ai_profile_repository import PostgresUserAIProfileRepository, UserAIProfileUnavailable
from routers import auth


class _Connection:
    def __init__(self):
        self.calls = []
        self.row = None

    def execute(self, sql, params):
        self.calls.append((sql, params))
        if "INSERT INTO" in sql:
            self.row = (*params[:12], json.loads(params[12]))
            return self
        return self

    def fetchone(self):
        sql = self.calls[-1][0]
        if "RETURNING user_id" in sql:
            return ("profile-user",)
        return self.row


def _repo(connection):
    @contextmanager
    def transaction(tenant):
        assert tenant == "profile-tenant"
        yield connection
    return PostgresUserAIProfileRepository(tenant_id="profile-tenant", user_id="profile-user", transaction_factory=transaction)


def test_normalized_repository_binds_runtime_tenant_and_user_without_app_state_or_ddl():
    connection = _Connection()
    repository = _repo(connection)
    assert repository.get() is None
    profile = {"userId": "profile-user", "tenantId": "profile-tenant", "provider": "groq", "model": "chosen-model",
               "status": "active", "allowSystemFallback": False, "createdAt": "2026-11-06T00:00:00Z",
               "updatedAt": "2026-11-06T00:00:00Z", "credentialRef": "vault-reference-fixture",
               "credentialBackend": "session_memory", "metadata": {"expiresOnLogout": True}}
    repository.save(profile)
    loaded = repository.get()
    assert loaded["allowSystemFallback"] is False and loaded["provider"] == "groq"
    assert loaded["credentialRef"] == "vault-reference-fixture"
    assert loaded["metadata"] == {"expiresOnLogout": True}
    assert repository.delete() is True
    sql = "\n".join(item[0] for item in connection.calls)
    assert "CREATE" not in sql and "app_state" not in sql
    assert sql.count("set_config('realytics.user_id'") == 4
    assert "WHERE tenant_id = %s AND user_id = %s" in sql
    assert all(params == ("profile-user",) for statement, params in connection.calls if "set_config" in statement)


def test_storage_postgres_profile_reads_never_touch_document_store(monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "postgres")
    connection = _Connection()
    monkeypatch.setattr("db.repositories.user_ai_profile_repository.PostgresUserAIProfileRepository",
                        lambda **_kwargs: _repo(connection))
    monkeypatch.setattr(storage, "_load_json_state", lambda: pytest.fail("app_state must not be read"))
    assert storage.get_user_ai_profile("profile-user", tenant_id="profile-tenant") is None
    saved = storage.upsert_user_ai_profile({"userId": "profile-user", "tenantId": "profile-tenant", "provider": "groq",
                                           "credentialRef": "vault-reference-fixture", "allowSystemFallback": False})
    assert saved["allowSystemFallback"] is False and "credentialRef" not in saved
    public = storage.get_user_ai_profile("profile-user", tenant_id="profile-tenant")
    assert public["credentialRefConfigured"] is True
    assert "vault-reference-fixture" not in json.dumps(public)
    assert storage.delete_user_ai_profile("profile-user", tenant_id="profile-tenant") is True


def test_profile_permission_failure_is_not_an_absent_profile_and_returns_safe_503(monkeypatch):
    @contextmanager
    def unavailable(_tenant):
        raise PermissionError("fixture SQL error must stay private")
        yield
    repository = PostgresUserAIProfileRepository(tenant_id="profile-tenant", user_id="profile-user", transaction_factory=unavailable)
    with pytest.raises(UserAIProfileUnavailable):
        repository.get()
    monkeypatch.setattr(auth, "identity_context_from_request", lambda _request: {
        "authenticated": True, "user": {"userId": "profile-user", "tenantId": "profile-tenant"}})
    monkeypatch.setattr(auth, "_get_user_ai_profile", lambda *_args, **_kwargs: repository.get())
    app = FastAPI()
    app.include_router(auth.router)
    response = TestClient(app).get("/auth/ai-profile")
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "ai_profile_authority_unavailable"
    assert "fixture SQL" not in response.text and "effectiveAi" not in response.json()


def test_profile_endpoint_reports_configured_system_policy_when_verified_profile_is_absent(monkeypatch):
    monkeypatch.setenv("REALYTICS_AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "configured-model-fixture")
    monkeypatch.setattr(auth, "identity_context_from_request", lambda _request: {
        "authenticated": True, "user": {"userId": "no-registered-profile", "tenantId": "profile-tenant"}})
    monkeypatch.setattr(auth, "_get_user_ai_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("use_cases.user_ai_profile.get_user_ai_profile", lambda *_args, **_kwargs: None)
    app = FastAPI()
    app.include_router(auth.router)
    response = TestClient(app).get("/auth/ai-profile")
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"] is None
    assert payload["fallbackPolicy"]["provider"] == payload["effectiveAi"]["provider"] == "openai"
    assert payload["fallbackPolicy"]["model"] == payload["effectiveAi"]["model"] == "configured-model-fixture"
