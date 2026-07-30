from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from db.repositories.identity_invitation_repository import (
    InvitationLifecycleError,
    PostgresIdentityInvitationRepository,
)
from schemas.identity_access import IssueTenantInvitationInput


NOW = datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)
TOKEN = "t" * 48
TERMS_VERSION = "realytics-terms-2026-07-30"
PRIVACY_VERSION = "realytics-privacy-2026-07-30"


class _Cursor:
    def __init__(self, fetches: list[Any] | None = None) -> None:
        self.fetches = list(fetches or [])
        self.executions: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.executions.append((sql, params))

    def fetchone(self):
        return self.fetches.pop(0)

    def fetchall(self):
        return self.fetches.pop(0) if self.fetches else []


class _Connection(AbstractContextManager):
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return self._cursor


def _repository(cursor: _Cursor) -> PostgresIdentityInvitationRepository:
    return PostgresIdentityInvitationRepository(
        transaction_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        now=lambda: NOW,
        token_factory=lambda: TOKEN,
    )


def _invitation_row(
    *,
    status: str = "pending",
    email: str | None = "invitee@example.com",
    expires_at: datetime | None = None,
):
    return (
        "inv-1",
        "tenant-a",
        "analyst",
        status,
        email,
        None,
        None,
        "https://issuer.example/auth/v1",
        "admin-subject",
        NOW - timedelta(hours=1),
        expires_at or NOW + timedelta(hours=24),
        None,
        None,
        None,
        None,
        None,
    )


def test_issue_stores_only_token_hash_and_returns_plaintext_once() -> None:
    cursor = _Cursor()
    issued = _repository(cursor).issue(
        tenant_id="tenant-a",
        payload=IssueTenantInvitationInput(
            role="analyst",
            invitedEmail="Invitee@Example.com",
        ),
        inviter_issuer="https://issuer.example/auth/v1/",
        inviter_subject="admin-subject",
    )

    assert issued.token == TOKEN
    assert issued.invitation["invitedEmail"] == "invitee@example.com"
    all_params = repr([params for _sql, params in cursor.executions])
    assert TOKEN not in all_params
    assert len({
        value
        for _sql, params in cursor.executions
        for value in params
        if isinstance(value, str) and len(value) == 64
    }) == 1
    assert any("invitation_issued" in repr(params) for _sql, params in cursor.executions)


def test_accept_is_atomic_and_role_comes_only_from_locked_invitation() -> None:
    cursor = _Cursor(
        [
            ("tenant-a", "inv-1", "pending", NOW + timedelta(hours=24)),
            _invitation_row(),
            (TERMS_VERSION, PRIVACY_VERSION),
            None,
            ("active",),
        ]
    )

    accepted = _repository(cursor).accept(
        token=TOKEN,
        principal_issuer="https://issuer.example/auth/v1/",
        principal_subject="new-user",
        principal_email="INVITEE@example.com",
        principal_display_name="Invitee",
        required_terms_version=TERMS_VERSION,
        required_privacy_version=PRIVACY_VERSION,
    )

    assert accepted == {
        "invitationId": "inv-1",
        "tenantId": "tenant-a",
        "role": "analyst",
        "status": "accepted",
        "membershipStatus": "active",
    }
    membership_insert = next(
        params
        for sql, params in cursor.executions
        if "INSERT INTO \"realytics\".tenant_memberships" in sql
    )
    assert membership_insert[3] == "analyst"
    assert membership_insert[4] == "invitation:inv-1"
    advisory_lock = next(
        (sql, params)
        for sql, params in cursor.executions
        if "pg_advisory_xact_lock" in sql
    )
    assert advisory_lock[1] == (
        "tenant-membership\x1ftenant-a\x1fhttps://issuer.example/auth/v1"
        "\x1fnew-user",
    )
    membership_lookup = next(
        sql
        for sql, _params in cursor.executions
        if 'FROM "realytics".tenant_memberships' in sql
    )
    assert "FOR UPDATE" not in membership_lookup
    assert sum("FOR UPDATE" in sql for sql, _params in cursor.executions) == 2


def test_accept_rejects_email_mismatch_before_principal_or_membership_write() -> None:
    cursor = _Cursor(
        [
            ("tenant-a", "inv-1", "pending", NOW + timedelta(hours=24)),
            _invitation_row(),
        ]
    )

    with pytest.raises(InvitationLifecycleError) as error:
        _repository(cursor).accept(
            token=TOKEN,
            principal_issuer="https://issuer.example/auth/v1",
            principal_subject="new-user",
            principal_email="attacker@example.com",
            principal_display_name="Attacker",
            required_terms_version=TERMS_VERSION,
            required_privacy_version=PRIVACY_VERSION,
        )

    assert error.value.code == "invitation_email_mismatch"
    sql = "\n".join(item[0] for item in cursor.executions)
    assert ".principals" not in sql
    assert ".tenant_memberships" not in sql


@pytest.mark.parametrize(
    ("profile_row", "expected_code"),
    [
        (None, "onboarding_profile_required"),
        (("stale-terms", PRIVACY_VERSION), "terms_version_mismatch"),
        ((TERMS_VERSION, "stale-privacy"), "privacy_version_mismatch"),
    ],
)
def test_accept_requires_current_profile_before_authority_write(
    profile_row,
    expected_code,
) -> None:
    cursor = _Cursor(
        [
            ("tenant-a", "inv-1", "pending", NOW + timedelta(hours=24)),
            _invitation_row(),
            profile_row,
        ]
    )

    with pytest.raises(InvitationLifecycleError) as error:
        _repository(cursor).accept(
            token=TOKEN,
            principal_issuer="https://issuer.example/auth/v1",
            principal_subject="new-user",
            principal_email="invitee@example.com",
            principal_display_name="Invitee",
            required_terms_version=TERMS_VERSION,
            required_privacy_version=PRIVACY_VERSION,
        )

    assert error.value.code == expected_code
    sql = "\n".join(item[0] for item in cursor.executions)
    assert 'FROM "realytics".onboarding_profiles' in sql
    assert 'INSERT INTO "realytics".principals' not in sql
    assert 'INSERT INTO "realytics".tenant_memberships' not in sql


def test_expired_invitation_is_persistently_closed_before_error_is_returned() -> None:
    cursor = _Cursor(
        [
            ("tenant-a", "inv-1", "pending", NOW - timedelta(seconds=1)),
            _invitation_row(expires_at=NOW - timedelta(seconds=1)),
        ]
    )

    with pytest.raises(InvitationLifecycleError) as error:
        _repository(cursor).accept(
            token=TOKEN,
            principal_issuer="https://issuer.example/auth/v1",
            principal_subject="new-user",
            principal_email="invitee@example.com",
            principal_display_name="Invitee",
            required_terms_version=TERMS_VERSION,
            required_privacy_version=PRIVACY_VERSION,
        )

    assert error.value.code == "invitation_expired"
    updates = [sql for sql, _params in cursor.executions if "UPDATE" in sql]
    assert any("tenant_invitations" in sql and "'expired'" in sql for sql in updates)
    assert any("identity_invitation_token_index" in sql and "'expired'" in sql for sql in updates)


def test_replayed_token_is_rejected_before_tenant_membership_lookup() -> None:
    cursor = _Cursor(
        [("tenant-a", "inv-1", "accepted", NOW + timedelta(hours=24))]
    )

    with pytest.raises(InvitationLifecycleError) as error:
        _repository(cursor).accept(
            token=TOKEN,
            principal_issuer="https://issuer.example/auth/v1",
            principal_subject="new-user",
            principal_email="invitee@example.com",
            principal_display_name="Invitee",
            required_terms_version=TERMS_VERSION,
            required_privacy_version=PRIVACY_VERSION,
        )

    assert error.value.code == "invitation_not_pending"
    assert len(cursor.executions) == 1
