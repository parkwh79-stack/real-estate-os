from contextlib import contextmanager
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from db.repositories.account_profile_repository import (
    AccountProfileNotActive, AccountProfileUnavailable, PostgresAccountProfileRepository,
)
from routers import auth


CONTEXT = {
    "authenticated": True,
    "membershipAuthority": {"source": "normalized_postgres"},
    "user": {"userId": "verified-user", "tenantId": "own-tenant", "status": "active", "role": "viewer",
             "metadata": {"issuer": "https://issuer.test/auth/v1", "subject": "signed-subject", "principalType": "human"}},
}


@pytest.fixture
def client(monkeypatch):
    context = deepcopy(CONTEXT)
    calls = []

    class Repository:
        error = None

        def update_display_name(self, **kwargs):
            calls.append(kwargs)
            if self.error:
                raise self.error
            return kwargs["display_name"]

    repository = Repository()
    monkeypatch.setattr(auth, "identity_context_from_request", lambda request: context)
    monkeypatch.setattr(auth, "account_profile_repository", lambda: repository)
    app = FastAPI()
    app.include_router(auth.router)
    with TestClient(app) as http:
        yield http, context, calls, repository


def test_viewer_can_edit_only_their_own_display_name_from_verified_identity(client):
    http, _, calls, _ = client
    response = http.put("/auth/profile", json={"displayName": "  새 검토자  "},
                        headers={"x-user-id": "other-user", "x-tenant-id": "other-tenant"})
    assert response.status_code == 200
    assert response.json() == {"schemaVersion": "realytics-account-profile-v1", "displayName": "새 검토자"}
    assert calls == [{"tenant_id": "own-tenant", "issuer": "https://issuer.test/auth/v1",
                      "subject": "signed-subject", "display_name": "새 검토자"}]


@pytest.mark.parametrize("body", [
    {"displayName": ""}, {"displayName": "   "}, {"displayName": "a" * 201},
    {"displayName": "ab\x00cd"}, {"displayName": 12},
    {"displayName": "11111111-2222-3333-4444-555555555555"},
    {"displayName": "signed-subject"}, {"displayName": "verified-user"},
    {"displayName": "새 이름", "userId": "other-user"},
    {"displayName": "새 이름", "role": "admin"},
    {"displayName": "새 이름", "tenantId": "other-tenant"},
])
def test_invalid_names_or_identity_mutation_never_reach_repository(client, body):
    http, _, calls, _ = client
    assert http.put("/auth/profile", json=body).status_code == 422
    assert calls == []


@pytest.mark.parametrize("alter,status", [
    (lambda c: c.update(authenticated=False), 401),
    (lambda c: c["user"].update(status="disabled"), 401),
    (lambda c: c["user"]["metadata"].update(principalType="service"), 403),
    (lambda c: c.update(membershipAuthority={"source": "verified_bearer_claims"}), 503),
    (lambda c: c["user"]["metadata"].pop("subject"), 503),
])
def test_unverified_or_inactive_accounts_cannot_edit_names(client, alter, status):
    http, context, calls, _ = client
    alter(context)
    assert http.put("/auth/profile", json={"displayName": "새 이름"}).status_code == status
    assert calls == []


@pytest.mark.parametrize("error,status,code", [
    (AccountProfileNotActive("private identity detail"), 403, "account_membership_not_active"),
    (AccountProfileUnavailable("private database detail"), 503, "account_profile_authority_unavailable"),
])
def test_repository_failure_is_not_success_and_has_no_private_details(client, error, status, code):
    http, _, _, repository = client
    repository.error = error
    response = http.put("/auth/profile", json={"displayName": "새 이름"})
    assert response.status_code == status
    assert response.json() == {"detail": {"error": code}}
    assert "private" not in response.text


def test_repository_rechecks_active_principal_membership_and_changes_only_name():
    calls = []

    class Connection:
        def execute(self, sql, params):
            calls.append((" ".join(sql.split()), params))
            return self

        def fetchone(self):
            return ("새 이름",)

    @contextmanager
    def transaction(tenant_id):
        assert tenant_id == "own-tenant"
        yield Connection()

    saved = PostgresAccountProfileRepository(transaction).update_display_name(
        tenant_id="own-tenant", issuer="verified-issuer", subject="verified-subject", display_name="새 이름")
    assert saved == "새 이름"
    sql, params = calls[0]
    assert params == ("새 이름", "verified-issuer", "verified-subject", "own-tenant")
    assert "principal.issuer = %s AND principal.subject = %s" in sql
    assert "membership.tenant_id = %s" in sql
    assert "membership.status = 'active' AND tenant.status = 'active'" in sql
    assert "principal.status = 'active' AND principal.principal_type = 'human'" in sql
    assert "SET display_name = %s, updated_at = statement_timestamp()" in sql


@pytest.mark.parametrize("result,error", [(None, AccountProfileNotActive), (("wrong name",), AccountProfileUnavailable)])
def test_repository_rejects_revocation_or_unverifiable_save(result, error):
    class Connection:
        def execute(self, *args):
            return self

        def fetchone(self):
            return result

    @contextmanager
    def transaction(_tenant):
        yield Connection()

    with pytest.raises(error):
        PostgresAccountProfileRepository(transaction).update_display_name(
            tenant_id="tenant", issuer="issuer", subject="subject", display_name="새 이름")
