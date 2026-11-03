import pytest
from fastapi.testclient import TestClient

from core.idp import IdpPrincipal
from db.repositories.default_workspace_repository import DefaultWorkspaceError, DefaultWorkspaceUnavailable
from main import app
from routers import auth


class _Repository:
    def __init__(self):
        self.calls = []
        self.error = None

    def ensure_for_principal(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"tenantId": "personal-" + "a" * 64, "role": "analyst", "created": True}


def _install(monkeypatch, *, enabled=True, claims=None, principal_type="human"):
    repository = _Repository()
    principal = IdpPrincipal(
        user_id="signed-subject", issuer="https://issuer.test/auth/v1", subject="signed-subject",
        tenant_id=None, email="verified@example.com", display_name="Verified Profile",
        role=None, provider="supabase", principal_type=principal_type, claims=claims or {},
    )
    monkeypatch.setenv("REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY", "postgres")
    if enabled:
        monkeypatch.setenv("REALYTICS_SIMPLE_SIGNUP_ENABLED", "true")
    else:
        monkeypatch.delenv("REALYTICS_SIMPLE_SIGNUP_ENABLED", raising=False)
    monkeypatch.setattr(auth, "principal_from_request", lambda _request: principal)
    monkeypatch.setattr(auth, "default_workspace_repository", lambda: repository)
    return repository


def test_default_capability_is_closed_without_the_flag_and_never_opens_a_database(monkeypatch):
    repository = _install(monkeypatch, enabled=False)
    client = TestClient(app)
    assert client.get("/auth/workspaces/default").json() == {
        "schemaVersion": "realytics-default-workspace-capability-v1", "enabled": False,
    }
    response = client.post("/auth/workspaces/default", json={})
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "simple_signup_disabled"
    assert repository.calls == []


def test_enabled_bootstrap_uses_only_the_verified_principal_and_fixed_dashboard(monkeypatch):
    repository = _install(monkeypatch, claims={
        "app_metadata": {"realytics_tenant_id": "forged-org", "realytics_role": "admin"},
        "user_metadata": {"role": "admin", "tenant": "forged-org"},
    })
    client = TestClient(app)
    assert client.get("/auth/workspaces/default").json()["enabled"] is True
    response = client.post("/auth/workspaces/default", json={}, headers={"X-REALYTICS-TENANT-ID": "forged-org"})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json() == {
        "schemaVersion": "realytics-default-workspace-v1",
        "tenantId": "personal-" + "a" * 64, "role": "analyst", "created": True, "next": "/dashboard",
    }
    assert repository.calls == [{
        "issuer": "https://issuer.test/auth/v1", "subject": "signed-subject",
        "email": "verified@example.com", "display_name": "Verified Profile",
    }]


@pytest.mark.parametrize("field", ["role", "tenantId", "issuer", "subject", "email", "next", "approved"])
def test_client_authority_inputs_are_rejected_before_any_mutation(monkeypatch, field):
    repository = _install(monkeypatch)
    response = TestClient(app).post("/auth/workspaces/default", json={field: "forged"})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "workspace_bootstrap_payload_forbidden"
    assert not repository.calls


@pytest.mark.parametrize("claims", [
    {"is_anonymous": True, "realytics_principal_type": "human"},
    {"grant_type": "client_credentials", "realytics_principal_type": "human"},
    {"gty": "client_credentials", "realytics_principal_type": "human"},
    {"gty": "authorization_code", "grant_type": "client_credentials", "realytics_principal_type": "human"},
    {"token_use": "client", "realytics_principal_type": "human"},
    {"idtyp": "app", "realytics_principal_type": "human"},
    {"realytics_principal_type": "service"},
    {"realytics_principal_type": "human", "app_metadata": {"realytics_principal_type": "service"}},
])
def test_guest_or_service_markers_cannot_be_overridden_by_a_human_label(monkeypatch, claims):
    repository = _install(monkeypatch, claims=claims)
    client = TestClient(app)
    assert client.get("/auth/workspaces/default").status_code == 403
    assert client.post("/auth/workspaces/default", json={}).status_code == 403
    assert not repository.calls


@pytest.mark.parametrize("principal_type", ["service", "unknown"])
def test_only_verified_humans_can_bootstrap(monkeypatch, principal_type):
    repository = _install(monkeypatch, principal_type=principal_type)
    assert TestClient(app).post("/auth/workspaces/default", json={}).status_code == 403
    assert not repository.calls


def test_missing_verified_bearer_is_not_a_guest_registration(monkeypatch):
    repository = _install(monkeypatch)
    monkeypatch.setattr(auth, "principal_from_request", lambda _request: None)
    client = TestClient(app)
    assert client.get("/auth/workspaces/default").status_code == 401
    assert client.post("/auth/workspaces/default", json={}).status_code == 401
    assert not repository.calls


@pytest.mark.parametrize("code,status", [
    ("tenant_selection_required", 400), ("principal_disabled", 403),
    ("no_active_tenant_membership", 403), ("invalid_membership_role", 403),
    ("default_workspace_conflict", 409),
])
def test_lifecycle_denials_remain_denials(monkeypatch, code, status):
    repository = _install(monkeypatch)
    repository.error = DefaultWorkspaceError(code)
    response = TestClient(app).post("/auth/workspaces/default", json={})
    assert response.status_code == status
    assert response.json()["detail"]["error"] == code


def test_repository_outage_is_fail_closed_and_does_not_expose_exception_details(monkeypatch):
    repository = _install(monkeypatch)
    repository.error = DefaultWorkspaceUnavailable("synthetic private endpoint detail")
    response = TestClient(app).post("/auth/workspaces/default", json={})
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "identity_membership_authority_unavailable"
    assert "private endpoint" not in response.text


def test_claims_compatibility_cannot_activate_the_normalized_bootstrap(monkeypatch):
    repository = _install(monkeypatch)
    monkeypatch.setenv("REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY", "claims")
    client = TestClient(app)
    assert client.get("/auth/workspaces/default").json()["enabled"] is False
    assert client.post("/auth/workspaces/default", json={}).status_code == 404
    assert not repository.calls
