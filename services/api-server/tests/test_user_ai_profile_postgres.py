from contextlib import contextmanager
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import storage
from db.repositories.user_ai_profile_repository import PostgresUserAIProfileRepository, UserAIProfileUnavailable
from routers import auth
from db.user_ai_profile_privileges import render_user_ai_profile_access_sql, render_user_ai_profile_access_rollback_sql


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


def test_approved_edit_manifest_is_scoped_and_has_guarded_rollback():
    sql = ";\n".join(render_user_ai_profile_access_sql())
    assert "AS RESTRICTIVE" in sql and "WITH CHECK" in sql
    assert "realytics.tenant_id" in sql and "realytics.user_id" in sql
    assert 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "realytics"."user_ai_profiles" TO "realytics_runtime"' in sql
    assert "GRANT ALL" not in sql and "CREATE TABLE" not in sql
    assert "conflicts with the approved contract" in sql and "rolbypassrls" in sql
    rollback = ";\n".join(render_user_ai_profile_access_rollback_sql())
    assert 'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE "realytics"."user_ai_profiles" FROM "realytics_runtime"' in rollback
    assert 'DROP POLICY user_scope_user_ai_profiles_runtime ON "realytics"."user_ai_profiles"' in rollback


def test_select_only_profile_get_projects_expiry_without_any_write(monkeypatch):
    raw = {"userId": "profile-user", "tenantId": "profile-tenant", "provider": "groq", "model": "fixture",
           "status": "active", "credentialRef": "stale-reference", "apiKeyConfigured": True, "allowSystemFallback": False}
    monkeypatch.setattr(auth, "identity_context_from_request", lambda _request: {"authenticated": True, "user": raw})
    monkeypatch.setattr(auth, "credential_session_binding_from_request", lambda *_a: {"sessionId": "profile-session"})
    monkeypatch.setattr(auth, "_get_user_ai_profile", lambda *_a, include_secret=False, **_kw:
                        dict(raw) if include_secret else {key: value for key, value in raw.items() if key != "credentialRef"})
    monkeypatch.setattr(auth, "credential_access_status", lambda *_a, **_kw: "expired")
    monkeypatch.setattr(auth, "_upsert_user_ai_profile", lambda *_a, **_kw: pytest.fail("GET cannot write a profile"))
    monkeypatch.setattr(auth, "revoke_credential", lambda *_a, **_kw: pytest.fail("GET cannot revoke a key"))
    monkeypatch.setattr(auth, "record_user_activity_log", lambda *_a, **_kw: pytest.fail("GET cannot append a cleanup event"))
    monkeypatch.setattr("use_cases.user_ai_profile.get_user_ai_profile", lambda *_a, **_kw: dict(raw))
    monkeypatch.setattr("use_cases.user_ai_profile.resolve_user_credential", lambda *_a, **_kw: None)
    app = FastAPI()
    app.include_router(auth.router)
    response = TestClient(app).get("/auth/ai-profile")
    assert response.status_code == 200
    assert response.json()["profile"]["credentialStatus"] == "expired"
    assert response.json()["profile"]["apiKeyConfigured"] is False
    assert response.json()["effectiveAi"]["directModelCall"] is False
    assert raw["credentialRef"] == "stale-reference"


def test_profile_clear_denied_by_read_only_authority_does_not_revoke_vault_key(monkeypatch):
    monkeypatch.setattr(auth, "identity_context_from_request", lambda _request: {"authenticated": True,
                        "user": {"userId": "profile-user", "tenantId": "profile-tenant"}})
    monkeypatch.setattr(auth, "credential_session_binding_from_request", lambda *_a: {"sessionId": "profile-session"})
    monkeypatch.setattr(auth, "_get_user_ai_profile", lambda *_a, **_kw: {"credentialRef": "stale-reference"})
    monkeypatch.setattr(auth, "credential_access_status", lambda *_a, **_kw: "expired")
    def unavailable(*_args, **_kwargs):
        raise UserAIProfileUnavailable("write permission denied")
    monkeypatch.setattr(auth, "_upsert_user_ai_profile", unavailable)
    monkeypatch.setattr(auth, "revoke_credential", lambda *_a, **_kw: pytest.fail("denied profile mutation must not revoke a key"))
    app = FastAPI()
    app.include_router(auth.router)
    response = TestClient(app).post("/auth/ai-profile", json={"clearApiKey": True})
    assert response.status_code == 503 and response.json()["detail"]["error"] == "ai_profile_authority_unavailable"


@pytest.mark.parametrize("status,session,expected", [("forbidden", "other-session", 409), ("active", None, 400), ("outage", "profile-session", 503)])
def test_profile_delete_rejects_unbound_other_session_or_unavailable_key_before_mutation(monkeypatch, status, session, expected):
    monkeypatch.setattr(auth, "identity_context_from_request", lambda _request: {"authenticated": True,
                        "user": {"userId": "profile-user", "tenantId": "profile-tenant"}})
    monkeypatch.setattr(auth, "credential_session_binding_from_request", lambda *_a: {"sessionId": session} if session else None)
    monkeypatch.setattr(auth, "_get_user_ai_profile", lambda *_a, **_kw: {"credentialRef": "owner-key-reference"})
    def access(*_args, **_kwargs):
        if status == "outage":
            raise RuntimeError("private vault fixture detail")
        return status
    monkeypatch.setattr(auth, "credential_access_status", access)
    monkeypatch.setattr(auth, "_delete_user_ai_profile", lambda *_a, **_kw: pytest.fail("rejected key boundary must not delete a profile"))
    monkeypatch.setattr(auth, "revoke_credential", lambda *_a, **_kw: pytest.fail("rejected key boundary must not revoke a key"))
    app = FastAPI()
    app.include_router(auth.router)
    response = TestClient(app).delete("/auth/ai-profile")
    assert response.status_code == expected and "private vault" not in response.text


@pytest.mark.parametrize("delete_result", [True, False, "denied"])
def test_profile_delete_only_revokes_its_bound_key_after_successful_database_delete(monkeypatch, delete_result):
    events = []
    monkeypatch.setattr(auth, "identity_context_from_request", lambda _request: {"authenticated": True,
                        "user": {"userId": "profile-user", "tenantId": "profile-tenant"}})
    monkeypatch.setattr(auth, "credential_session_binding_from_request", lambda *_a: {"sessionId": "profile-session"})
    monkeypatch.setattr(auth, "_get_user_ai_profile", lambda *_a, **_kw: {"credentialRef": "owner-key-reference"})
    monkeypatch.setattr(auth, "credential_access_status", lambda *_a, **_kw: "active")
    def delete(user_id, *, tenant_id):
        assert (user_id, tenant_id) == ("profile-user", "profile-tenant")
        events.append("database-delete")
        if delete_result == "denied":
            raise UserAIProfileUnavailable("private SQL failure")
        return delete_result
    def revoke(reference, **boundary):
        assert events == ["database-delete"] and delete_result is True
        assert reference == "owner-key-reference"
        assert boundary == {"user_id": "profile-user", "tenant_id": "profile-tenant", "session_id": "profile-session"}
        events.append("key-revoke")
        return True
    monkeypatch.setattr(auth, "_delete_user_ai_profile", delete)
    monkeypatch.setattr(auth, "revoke_credential", revoke)
    monkeypatch.setattr(auth, "record_user_activity_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(auth, "build_user_ai_context", lambda **_kw: {})
    monkeypatch.setattr(auth, "safe_user_ai_context_summary", lambda _context: {})
    app = FastAPI()
    app.include_router(auth.router)
    response = TestClient(app).delete("/auth/ai-profile")
    if delete_result == "denied":
        assert response.status_code == 503 and events == ["database-delete"]
        assert "private SQL" not in response.text
    else:
        assert response.status_code == 200 and response.json()["deleted"] is delete_result
        assert events == (["database-delete", "key-revoke"] if delete_result else ["database-delete"])


@pytest.mark.parametrize("action", ["--render", "--rollback-sql"])
def test_profile_provisioner_can_render_without_any_database_connection(monkeypatch, capsys, action):
    from scripts import provision_user_ai_profiles as provisioner
    monkeypatch.setattr(provisioner.postgres, "connect", lambda *_a, **_kw: pytest.fail("SQL rendering must not connect"))
    assert provisioner.main([action, "--role", "realytics_runtime"]) == 0
    sql = capsys.readouterr().out
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    assert "user_ai_profiles" in sql and 'TO "anon"' not in sql and "CREATE TABLE" not in sql
    assert ("DROP POLICY user_scope_user_ai_profiles_runtime" in sql) is (action == "--rollback-sql")


def test_profile_provisioner_rolls_back_a_policy_collision_before_grants(monkeypatch):
    from scripts import provision_user_ai_profiles as provisioner
    class Cursor:
        statements = []
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def execute(self, statement, _params=None):
            self.statements.append(statement)
            if statement.startswith("DO $realytics_user_ai_profile$"):
                raise RuntimeError("policy collision fixture")
        def fetchone(self):
            return ("realytics",)
    class Connection:
        rolled_back = closed = False
        cursor_value = Cursor()
        def cursor(self):
            return self.cursor_value
        def commit(self):
            pytest.fail("failed policy must not commit")
        def rollback(self):
            self.rolled_back = True
        def close(self):
            self.closed = True
    connection = Connection()
    monkeypatch.setenv("REALYTICS_MIGRATION_DATABASE_URL", "postgresql://synthetic.invalid/test")
    monkeypatch.setattr(provisioner.postgres, "connect", lambda *_a, **_kw: connection)
    with pytest.raises(RuntimeError, match="policy collision fixture"):
        provisioner.main(["--apply", "--role", "realytics_runtime"])
    assert connection.rolled_back and connection.closed
    assert not any(statement.startswith("GRANT") for statement in connection.cursor_value.statements)
