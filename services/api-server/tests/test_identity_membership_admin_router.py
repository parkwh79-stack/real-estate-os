from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.testclient import TestClient

from db.repositories.identity_membership_admin_repository import (
    MembershipRoleChangeError,
)
from main import app
from routers import auth


NOW = datetime(2026, 7, 30, 2, 0, tzinfo=timezone.utc)


def _context(*, normalized: bool = True):
    return {
        "authenticated": True,
        "role": "admin",
        "permissions": ["user:manage"],
        "user": {
            "tenantId": "tenant-a",
            "metadata": {
                "issuer": "https://issuer.example/auth/v1",
                "subject": "admin-subject",
                "principalType": "human",
            },
        },
        "identityAssurance": {
            "mfaVerified": True,
            "authenticatedAt": NOW.isoformat(),
        },
        "membershipAuthority": {
            "source": (
                "normalized_postgres"
                if normalized
                else "verified_bearer_claims"
            ),
        },
    }


def _membership(*, role: str = "analyst", version: int = 4):
    return {
        "tenantId": "tenant-a",
        "issuer": "https://issuer.example/auth/v1",
        "subject": "target-subject",
        "principalType": "human",
        "principalStatus": "active",
        "email": "target@example.com",
        "displayName": "Target User",
        "role": role,
        "status": "active",
        "membershipVersion": version,
        "updatedAt": NOW,
    }


class _Repository:
    def __init__(self) -> None:
        self.list_call = None
        self.change_call = None

    def list_for_tenant(self, **kwargs):
        self.list_call = kwargs
        return [_membership()]

    def change_role(self, **kwargs):
        self.change_call = kwargs
        return _membership(role="reviewer", version=5)


def test_membership_list_requires_user_manage_and_normalized_tenant(
    monkeypatch,
) -> None:
    repository = _Repository()
    permissions = []

    def authorize(_request, permission):
        permissions.append(permission)
        return _context()

    monkeypatch.setattr(auth, "authorize_request", authorize)
    monkeypatch.setattr(
        auth,
        "identity_membership_administration_repository",
        lambda: repository,
    )

    response = TestClient(app).get("/auth/memberships?limit=25")

    assert response.status_code == 200
    assert permissions == ["user:manage"]
    assert repository.list_call == {"tenant_id": "tenant-a", "limit": 25}
    body = response.json()
    assert body["schemaVersion"] == "realytics-tenant-membership-list-v1"
    assert body["memberships"][0]["membershipVersion"] == 4
    assert set(body["memberships"][0]) == {
        "tenantId",
        "issuer",
        "subject",
        "principalType",
        "principalStatus",
        "email",
        "displayName",
        "role",
        "status",
        "membershipVersion",
        "updatedAt",
    }


def test_role_change_requires_aal2_and_uses_verified_actor_key(monkeypatch) -> None:
    repository = _Repository()
    step_up_actions = []
    monkeypatch.setattr(auth, "authorize_request", lambda *_args: _context())
    monkeypatch.setattr(
        auth,
        "require_step_up_authentication",
        lambda _context, *, action: step_up_actions.append(action),
    )
    monkeypatch.setattr(
        auth,
        "identity_membership_administration_repository",
        lambda: repository,
    )

    response = TestClient(app).patch(
        "/auth/memberships/role",
        json={
            "targetIssuer": "https://issuer.example/auth/v1/",
            "targetSubject": "target-subject",
            "expectedMembershipVersion": 4,
            "role": "reviewer",
            "reason": "Approved role adjustment",
        },
    )

    assert response.status_code == 200
    assert step_up_actions == ["tenant_membership_role_change"]
    assert repository.change_call["tenant_id"] == "tenant-a"
    assert repository.change_call["actor_issuer"] == (
        "https://issuer.example/auth/v1"
    )
    assert repository.change_call["actor_subject"] == "admin-subject"
    assert repository.change_call["payload"].role.value == "reviewer"
    assert response.json()["membership"]["membershipVersion"] == 5


def test_role_change_preserves_step_up_failure_contract(monkeypatch) -> None:
    repository = _Repository()
    monkeypatch.setattr(auth, "authorize_request", lambda *_args: _context())

    def reject_step_up(_context, *, action):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "step_up_required",
                "action": action,
                "reason": "aal2_required",
            },
        )

    monkeypatch.setattr(auth, "require_step_up_authentication", reject_step_up)
    monkeypatch.setattr(
        auth,
        "identity_membership_administration_repository",
        lambda: repository,
    )

    response = TestClient(app).patch(
        "/auth/memberships/role",
        json={
            "targetIssuer": "https://issuer.example/auth/v1",
            "targetSubject": "target-subject",
            "expectedMembershipVersion": 4,
            "role": "reviewer",
            "reason": "Approved role adjustment",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "error": "step_up_required",
        "action": "tenant_membership_role_change",
        "reason": "aal2_required",
    }
    assert repository.change_call is None


def test_membership_admin_rejects_claims_compatibility_authority(monkeypatch) -> None:
    monkeypatch.setattr(
        auth,
        "authorize_request",
        lambda *_args: _context(normalized=False),
    )

    response = TestClient(app).get("/auth/memberships")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error": "membership_authority_not_normalized"
    }


def test_role_change_maps_cas_failure_to_stable_conflict(monkeypatch) -> None:
    class _ConflictRepository(_Repository):
        def change_role(self, **_kwargs):
            raise MembershipRoleChangeError("membership_version_conflict")

    monkeypatch.setattr(auth, "authorize_request", lambda *_args: _context())
    monkeypatch.setattr(
        auth,
        "require_step_up_authentication",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auth,
        "identity_membership_administration_repository",
        lambda: _ConflictRepository(),
    )

    response = TestClient(app).patch(
        "/auth/memberships/role",
        json={
            "targetIssuer": "https://issuer.example/auth/v1",
            "targetSubject": "target-subject",
            "expectedMembershipVersion": 4,
            "role": "reviewer",
            "reason": "Approved role adjustment",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error": "membership_version_conflict"
    }
