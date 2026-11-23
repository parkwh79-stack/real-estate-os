from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from core.idp import IdpPrincipal
from db.repositories.onboarding_repository import (
    OnboardingLifecycleError,
    OnboardingRepositoryUnavailable,
)
from main import app
from routers import auth


NOW = datetime(2026, 7, 30, 5, 0, tzinfo=timezone.utc)
ISSUER = "https://project.supabase.co/auth/v1"


def _principal(*, aal: str = "aal2") -> IdpPrincipal:
    timestamp = datetime.now(timezone.utc).timestamp()
    return IdpPrincipal(
        user_id="signup-subject",
        issuer=ISSUER,
        subject="signup-subject",
        tenant_id=None,
        email="signup@example.com",
        display_name="Signup User",
        role=None,
        provider="supabase",
        principal_type="human",
        claims={
            "iss": ISSUER,
            "sub": "signup-subject",
            "aal": aal,
            "auth_time": timestamp,
            "amr": [{"method": "totp", "timestamp": timestamp}],
        },
    )


def _profile(version: int = 1):
    return {
        "issuer": ISSUER,
        "subject": "signup-subject",
        "contactEmail": "signup@example.com",
        "displayName": "Signup User",
        "phone": None,
        "jobTitle": "Analyst",
        "termsVersion": "realytics-terms-2026-07-30",
        "privacyVersion": "realytics-privacy-2026-07-30",
        "termsAcceptedAt": NOW,
        "privacyAcceptedAt": NOW,
        "profileVersion": version,
        "updatedAt": NOW,
    }


def _request(*, status: str = "pending", version: int = 1):
    return {
        "requestId": "access-request-1",
        "requestType": "existing_organization",
        "requesterIssuer": ISSUER,
        "requesterSubject": "signup-subject",
        "requesterDisplayName": "Signup User",
        "requesterEmail": "signup@example.com",
        "profileVersionSnapshot": 1,
        "termsVersionSnapshot": "realytics-terms-2026-07-30",
        "privacyVersionSnapshot": "realytics-privacy-2026-07-30",
        "profileSnapshotHash": "a" * 64,
        "organizationId": "tenant-a",
        "organizationName": "Acme Capital",
        "businessRegistrationNumber": None,
        "requestedRole": "analyst",
        "message": "Please review",
        "status": status,
        "requestVersion": version,
        "submittedAt": NOW,
        "decisionAt": None,
        "decisionReason": None,
        "approvedOrganizationId": None,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


class _Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get_status(self, **kwargs):
        self.calls.append(("get_status", kwargs))
        return {
            "profile": None,
            "accessRequests": [],
            "memberships": [],
            "platformOperator": False,
            "nextAction": "complete_profile",
        }

    def save_profile(self, **kwargs):
        self.calls.append(("save_profile", kwargs))
        return _profile()

    def search_organizations(self, **kwargs):
        self.calls.append(("search_organizations", kwargs))
        return [{"organizationId": "tenant-a", "name": "Acme Capital"}]

    def create_request(self, **kwargs):
        self.calls.append(("create_request", kwargs))
        return _request()

    def resubmit_request(self, **kwargs):
        self.calls.append(("resubmit_request", kwargs))
        return _request(version=3)

    def list_reviewable(self, **kwargs):
        self.calls.append(("list_reviewable", kwargs))
        return "tenant", [_request()]

    def decide_request(self, **kwargs):
        self.calls.append(("decide_request", kwargs))
        result = _request(status="approved", version=2)
        result["approvedOrganizationId"] = "tenant-a"
        result["decisionReason"] = "Approved for pilot"
        result["decisionAt"] = NOW
        return result


def _install(monkeypatch, repository: _Repository, *, aal: str = "aal2"):
    monkeypatch.setattr(auth, "principal_from_request", lambda _request: _principal(aal=aal))
    monkeypatch.setattr(auth, "onboarding_repository", lambda: repository)


def test_memberless_status_uses_verified_principal_without_membership_resolution(
    monkeypatch,
) -> None:
    repository = _Repository()
    _install(monkeypatch, repository)

    response = TestClient(app).get("/auth/onboarding/status")

    assert response.status_code == 200
    assert response.json()["accessState"] == "onboarding_required"
    assert response.json()["nextAction"] == "complete_profile"
    assert response.json()["policyVersions"] == {
        "termsVersion": "realytics-terms-2026-07-30",
        "privacyVersion": "realytics-privacy-2026-07-30",
    }
    assert repository.calls == [
        (
            "get_status",
            {"issuer": ISSUER, "subject": "signup-subject"},
        )
    ]


def test_onboarding_requires_verified_human_bearer(monkeypatch) -> None:
    repository = _Repository()
    monkeypatch.setattr(auth, "principal_from_request", lambda _request: None)
    monkeypatch.setattr(auth, "onboarding_repository", lambda: repository)

    response = TestClient(app).get("/auth/onboarding/status")

    assert response.status_code == 401
    assert response.json()["detail"] == {"error": "authentication_required"}
    assert repository.calls == []


def test_profile_acceptance_rejects_client_forged_policy_version(
    monkeypatch,
) -> None:
    repository = _Repository()
    _install(monkeypatch, repository)

    response = TestClient(app).put(
        "/auth/onboarding/profile",
        json={
            "displayName": "Signup User",
            "jobTitle": "Analyst",
            "termsAccepted": True,
            "privacyAccepted": True,
            "termsVersion": "client-chosen-version",
            "privacyVersion": "realytics-privacy-2026-07-30",
            "expectedProfileVersion": 0,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "terms_version_mismatch"
    assert response.json()["detail"]["currentPolicyVersions"] == {
        "termsVersion": "realytics-terms-2026-07-30",
        "privacyVersion": "realytics-privacy-2026-07-30",
    }
    assert repository.calls == []


def test_profile_acceptance_rejects_stale_privacy_policy_version(
    monkeypatch,
) -> None:
    repository = _Repository()
    _install(monkeypatch, repository)
    monkeypatch.setenv("REALYTICS_PRIVACY_VERSION", "privacy-current-v2")

    response = TestClient(app).put(
        "/auth/onboarding/profile",
        json={
            "displayName": "Signup User",
            "termsAccepted": True,
            "privacyAccepted": True,
            "termsVersion": "realytics-terms-2026-07-30",
            "privacyVersion": "realytics-privacy-2026-07-30",
            "expectedProfileVersion": 0,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error": "privacy_version_mismatch",
        "currentPolicyVersions": {
            "termsVersion": "realytics-terms-2026-07-30",
            "privacyVersion": "privacy-current-v2",
        },
    }
    assert repository.calls == []


def test_profile_acceptance_persists_only_current_server_versions(
    monkeypatch,
) -> None:
    repository = _Repository()
    _install(monkeypatch, repository)

    response = TestClient(app).put(
        "/auth/onboarding/profile",
        json={
            "displayName": "Signup User",
            "jobTitle": "Analyst",
            "termsAccepted": True,
            "privacyAccepted": True,
            "termsVersion": "realytics-terms-2026-07-30",
            "privacyVersion": "realytics-privacy-2026-07-30",
            "expectedProfileVersion": 0,
        },
    )

    assert response.status_code == 200
    name, call = repository.calls[0]
    assert name == "save_profile"
    assert call["issuer"] == ISSUER
    assert call["subject"] == "signup-subject"
    assert call["contact_email"] == "signup@example.com"
    assert call["payload"].termsAccepted is True
    assert response.json()["profile"]["profileVersion"] == 1


def test_request_submission_uses_verified_pair_and_defaults_analyst(
    monkeypatch,
) -> None:
    repository = _Repository()
    _install(monkeypatch, repository)

    response = TestClient(app).post(
        "/auth/onboarding/requests",
        json={
            "requestType": "existing_organization",
            "organizationId": "tenant-a",
            "message": "Please review",
        },
    )

    assert response.status_code == 201
    name, call = repository.calls[0]
    assert name == "create_request"
    assert call["issuer"] == ISSUER
    assert call["subject"] == "signup-subject"
    assert call["payload"].requestedRole.value == "analyst"
    assert response.json()["request"]["requestId"] == "access-request-1"


def test_new_organization_requires_and_normalizes_business_number(
    monkeypatch,
) -> None:
    repository = _Repository()
    _install(monkeypatch, repository)

    missing = TestClient(app).post(
        "/auth/onboarding/requests",
        json={
            "requestType": "new_organization",
            "organizationName": "Acme Capital",
        },
    )
    assert missing.status_code == 422
    assert repository.calls == []

    accepted = TestClient(app).post(
        "/auth/onboarding/requests",
        json={
            "requestType": "new_organization",
            "organizationName": "Acme Capital",
            "businessRegistrationNumber": "123-45-67890",
        },
    )
    assert accepted.status_code == 201
    assert repository.calls[0][1][
        "payload"
    ].businessRegistrationNumber == "1234567890"


def test_review_list_passes_tenant_selector_for_database_admin_check(
    monkeypatch,
) -> None:
    repository = _Repository()
    _install(monkeypatch, repository)

    response = TestClient(app).get(
        "/auth/access-requests?status=all",
        headers={"x-realytics-tenant-id": "tenant-a"},
    )

    assert response.status_code == 200
    name, call = repository.calls[0]
    assert name == "list_reviewable"
    assert call["tenant_id"] == "tenant-a"
    assert call["status"] == "all"
    assert response.json()["reviewScope"] == "tenant"


def test_decision_requires_signed_aal2_before_repository(monkeypatch) -> None:
    repository = _Repository()
    _install(monkeypatch, repository, aal="aal1")

    response = TestClient(app).post(
        "/auth/access-requests/access-request-1/decision",
        json={
            "decision": "approve",
            "expectedRequestVersion": 1,
            "assignedRole": "analyst",
            "reason": "Approved for pilot",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "step_up_required"
    assert response.json()["detail"]["reason"] == "aal2_required"
    assert repository.calls == []


def test_aal2_decision_uses_verified_actor_and_cas(monkeypatch) -> None:
    repository = _Repository()
    _install(monkeypatch, repository, aal="aal2")
    monkeypatch.setenv("REALYTICS_STEP_UP_MAX_AGE_SECONDS", "900")

    response = TestClient(app).post(
        "/auth/access-requests/access-request-1/decision",
        json={
            "decision": "approve",
            "expectedRequestVersion": 1,
            "assignedRole": "viewer",
            "reason": "Approved for pilot",
        },
    )

    assert response.status_code == 200
    name, call = repository.calls[0]
    assert name == "decide_request"
    assert call["actor_issuer"] == ISSUER
    assert call["actor_subject"] == "signup-subject"
    assert call["payload"].expectedRequestVersion == 1
    assert call["payload"].assignedRole.value == "viewer"
    assert "decisionByIssuer" not in response.json()["request"]
    assert "decisionBySubject" not in response.json()["request"]


def test_lifecycle_and_identity_pool_unavailability_have_stable_errors(
    monkeypatch,
) -> None:
    class _ConflictRepository(_Repository):
        def create_request(self, **_kwargs):
            raise OnboardingLifecycleError("tenant_access_request_pending")

    _install(monkeypatch, _ConflictRepository())
    conflict = TestClient(app).post(
        "/auth/onboarding/requests",
        json={
            "requestType": "new_organization",
            "organizationName": "Acme Capital",
            "businessRegistrationNumber": "123-45-67890",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "error": "tenant_access_request_pending"
    }

    class _UnavailableRepository(_Repository):
        def get_status(self, **_kwargs):
            raise OnboardingRepositoryUnavailable("offline")

    _install(monkeypatch, _UnavailableRepository())
    unavailable = TestClient(app).get("/auth/onboarding/status")
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == {
        "error": "onboarding_authority_unavailable"
    }
