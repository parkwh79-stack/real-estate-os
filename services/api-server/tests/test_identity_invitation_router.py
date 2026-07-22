from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from core.idp import IdpPrincipal
from db.repositories.identity_invitation_repository import (
    InvitationLifecycleError,
    IssuedInvitation,
)
from main import app
from routers import auth


NOW = datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)


def _context(*, aal2: bool = True):
    return {
        "authenticated": True,
        "role": "admin",
        "permissions": ["user:manage"],
        "user": {
            "tenantId": "tenant-a",
            "metadata": {
                "issuer": "https://project.supabase.co/auth/v1",
                "subject": "admin-subject",
                "principalType": "human",
            },
        },
        "identityAssurance": {
            "mfaVerified": aal2,
            "authenticatedAt": NOW.isoformat(),
        },
    }


def _principal():
    return IdpPrincipal(
        user_id="new-user",
        issuer="https://project.supabase.co/auth/v1",
        subject="new-subject",
        tenant_id=None,
        email="invitee@example.com",
        display_name="Invitee",
        role=None,
        provider="supabase",
        principal_type="human",
        claims={},
    )


class _Repository:
    def __init__(self) -> None:
        self.accepted = None

    def issue(self, **kwargs):
        return IssuedInvitation(
            token="t" * 48,
            invitation={
                "invitationId": "inv-1",
                "tenantId": kwargs["tenant_id"],
                "role": kwargs["payload"].role.value,
                "status": "pending",
                "invitedEmail": kwargs["payload"].invitedEmail,
                "invitedIssuer": None,
                "invitedSubject": None,
                "issuedByIssuer": kwargs["inviter_issuer"],
                "issuedBySubject": kwargs["inviter_subject"],
                "issuedAt": NOW,
                "expiresAt": NOW + timedelta(hours=72),
                "acceptedByIssuer": None,
                "acceptedBySubject": None,
                "acceptedAt": None,
                "revokedAt": None,
                "revocationReason": None,
            },
        )

    def accept(self, **kwargs):
        self.accepted = kwargs
        return {
            "invitationId": "inv-1",
            "tenantId": "tenant-a",
            "role": "analyst",
            "status": "accepted",
            "membershipStatus": "active",
        }


def test_sensitive_invitation_issue_uses_verified_context_and_step_up(monkeypatch):
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
        "identity_invitation_repository",
        lambda: repository,
    )

    response = TestClient(app).post(
        "/auth/invitations",
        json={"role": "admin", "invitedEmail": "invitee@example.com"},
    )

    assert response.status_code == 201
    assert response.json()["token"] == "t" * 48
    assert response.json()["tokenPolicy"] == "shown_once_never_persisted_in_plaintext"
    assert step_up_actions == ["tenant_invitation_issue_sensitive"]


def test_accept_allows_verified_principal_before_membership_exists(monkeypatch):
    repository = _Repository()
    monkeypatch.setattr(auth, "principal_from_request", lambda _request: _principal())
    monkeypatch.setattr(
        auth,
        "identity_invitation_repository",
        lambda: repository,
    )

    response = TestClient(app).post(
        "/auth/invitations/accept",
        headers={"authorization": "Bearer verified"},
        json={"token": "t" * 48},
    )

    assert response.status_code == 200
    assert response.json()["invitation"]["membershipStatus"] == "active"
    assert repository.accepted["principal_subject"] == "new-subject"
    assert repository.accepted["principal_email"] == "invitee@example.com"


def test_invitation_replay_has_stable_conflict_error(monkeypatch):
    class _ReplayRepository(_Repository):
        def accept(self, **_kwargs):
            raise InvitationLifecycleError("invitation_not_pending")

    monkeypatch.setattr(auth, "principal_from_request", lambda _request: _principal())
    monkeypatch.setattr(
        auth,
        "identity_invitation_repository",
        lambda: _ReplayRepository(),
    )

    response = TestClient(app).post(
        "/auth/invitations/accept",
        headers={"authorization": "Bearer verified"},
        json={"token": "t" * 48},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {"error": "invitation_not_pending"}
