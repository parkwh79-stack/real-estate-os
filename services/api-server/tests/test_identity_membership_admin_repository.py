from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

import pytest

from db.repositories.identity_membership_admin_repository import (
    MembershipRoleChangeError,
    PostgresIdentityMembershipAdministrationRepository,
)
from schemas.identity_access import ChangeTenantMembershipRoleInput


NOW = datetime(2026, 7, 30, 2, 0, tzinfo=timezone.utc)


def _membership_row(
    *,
    issuer: str = "https://issuer.example/auth/v1",
    subject: str = "target-subject",
    principal_type: str = "human",
    principal_status: str = "active",
    role: str = "analyst",
    status: str = "active",
    version: int = 4,
):
    return (
        "tenant-a",
        issuer,
        subject,
        principal_type,
        principal_status,
        "target@example.com",
        "Target User",
        role,
        status,
        version,
        NOW,
    )


class _Cursor:
    def __init__(
        self,
        *,
        fetchones: list[Any] | None = None,
        fetchalls: list[Any] | None = None,
    ) -> None:
        self.fetchones = list(fetchones or [])
        self.fetchalls = list(fetchalls or [])
        self.executions: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.executions.append((sql, params))

    def fetchone(self):
        return self.fetchones.pop(0) if self.fetchones else None

    def fetchall(self):
        return self.fetchalls.pop(0) if self.fetchalls else []


class _Connection(AbstractContextManager):
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def _repository(cursor: _Cursor):
    tenant_calls: list[str] = []

    def transaction(tenant_id: str):
        tenant_calls.append(tenant_id)
        return _Connection(cursor)

    repository = PostgresIdentityMembershipAdministrationRepository(
        transaction_factory=transaction,
        schema_name="realytics",
        now=lambda: NOW,
        event_id_factory=lambda: "identity-event-1",
    )
    return repository, tenant_calls


def _payload(
    *,
    role: str = "reviewer",
    issuer: str = "https://issuer.example/auth/v1/",
    subject: str = "target-subject",
    version: int = 4,
):
    return ChangeTenantMembershipRoleInput(
        targetIssuer=issuer,
        targetSubject=subject,
        expectedMembershipVersion=version,
        role=role,
        reason="Approved operational role adjustment",
    )


def test_list_is_tenant_scoped_and_returns_only_typed_directory_fields() -> None:
    cursor = _Cursor(fetchalls=[[_membership_row()]])
    repository, tenant_calls = _repository(cursor)

    rows = repository.list_for_tenant(tenant_id="tenant-a", limit=25)

    assert tenant_calls == ["tenant-a"]
    assert rows == [
        {
            "tenantId": "tenant-a",
            "issuer": "https://issuer.example/auth/v1",
            "subject": "target-subject",
            "principalType": "human",
            "principalStatus": "active",
            "email": "target@example.com",
            "displayName": "Target User",
            "role": "analyst",
            "status": "active",
            "membershipVersion": 4,
            "updatedAt": NOW,
        }
    ]
    sql, params = cursor.executions[0]
    assert "WHERE membership.tenant_id = %s" in sql
    assert params == ("tenant-a", 25)
    assert "verification_method" not in sql
    assert "legacy_user_id" not in sql


def test_role_change_uses_cas_and_appends_minimal_audit_event_atomically() -> None:
    cursor = _Cursor(
        fetchones=[
            _membership_row(),
            (5, NOW),
        ]
    )
    repository, tenant_calls = _repository(cursor)

    changed = repository.change_role(
        tenant_id="tenant-a",
        actor_issuer="https://issuer.example/auth/v1/",
        actor_subject="admin-subject",
        payload=_payload(),
    )

    assert tenant_calls == ["tenant-a"]
    assert changed["role"] == "reviewer"
    assert changed["membershipVersion"] == 5
    advisory = next(
        params
        for sql, params in cursor.executions
        if "pg_advisory_xact_lock" in sql
    )
    assert advisory == ("tenant-membership-role\x1ftenant-a",)
    update_sql, update_params = next(
        (sql, params)
        for sql, params in cursor.executions
        if 'UPDATE "realytics".tenant_memberships' in sql
    )
    assert "membership_version = membership_version + 1" in update_sql
    assert "AND membership_version = %s" in update_sql
    assert update_params[-1] == 4
    event_params = next(
        params
        for sql, params in cursor.executions
        if "tenant_identity_access_events" in sql and "INSERT INTO" in sql
    )
    assert event_params[0:6] == (
        "tenant-a",
        "identity-event-1",
        "https://issuer.example/auth/v1",
        "admin-subject",
        "https://issuer.example/auth/v1",
        "target-subject",
    )
    event_payload = event_params[6]
    assert '"previousRole":"analyst"' in event_payload
    assert '"role":"reviewer"' in event_payload
    assert "target@example.com" not in event_payload
    assert "oauth" not in event_payload.casefold()


@pytest.mark.parametrize(
    ("actor_subject", "payload", "expected_code"),
    [
        (
            "target-subject",
            _payload(),
            "membership_self_change_forbidden",
        ),
        (
            "admin-subject",
            _payload(version=3),
            "membership_version_conflict",
        ),
        (
            "admin-subject",
            _payload(role="analyst"),
            "membership_role_unchanged",
        ),
    ],
)
def test_role_change_rejects_self_stale_and_noop_commands(
    actor_subject: str,
    payload: ChangeTenantMembershipRoleInput,
    expected_code: str,
) -> None:
    cursor = _Cursor(fetchones=[_membership_row()])
    repository, _tenant_calls = _repository(cursor)

    with pytest.raises(MembershipRoleChangeError) as error:
        repository.change_role(
            tenant_id="tenant-a",
            actor_issuer="https://issuer.example/auth/v1",
            actor_subject=actor_subject,
            payload=payload,
        )

    assert error.value.code == expected_code
    sql = "\n".join(statement for statement, _params in cursor.executions)
    assert 'UPDATE "realytics".tenant_memberships' not in sql
    assert "tenant_identity_access_events" not in sql


def test_role_change_protects_last_active_tenant_admin() -> None:
    cursor = _Cursor(
        fetchones=[
            _membership_row(role="admin"),
            (1,),
        ]
    )
    repository, _tenant_calls = _repository(cursor)

    with pytest.raises(MembershipRoleChangeError) as error:
        repository.change_role(
            tenant_id="tenant-a",
            actor_issuer="https://issuer.example/auth/v1",
            actor_subject="other-admin",
            payload=_payload(role="reviewer"),
        )

    assert error.value.code == "last_tenant_admin_role_change_forbidden"
    sql = "\n".join(statement for statement, _params in cursor.executions)
    assert "role = 'admin'" in sql
    assert 'UPDATE "realytics".tenant_memberships' not in sql


def test_role_change_rejects_non_active_target() -> None:
    cursor = _Cursor(fetchones=[_membership_row(status="suspended")])
    repository, _tenant_calls = _repository(cursor)

    with pytest.raises(MembershipRoleChangeError) as error:
        repository.change_role(
            tenant_id="tenant-a",
            actor_issuer="https://issuer.example/auth/v1",
            actor_subject="admin-subject",
            payload=_payload(),
        )

    assert error.value.code == "tenant_membership_not_active"


@pytest.mark.parametrize(
    ("row", "expected_code"),
    [
        (
            _membership_row(principal_status="revoked"),
            "target_principal_not_active",
        ),
        (
            _membership_row(principal_type="service"),
            "target_principal_not_human",
        ),
    ],
)
def test_role_change_rejects_inactive_or_service_principal(
    row: tuple[Any, ...],
    expected_code: str,
) -> None:
    cursor = _Cursor(fetchones=[row])
    repository, _tenant_calls = _repository(cursor)

    with pytest.raises(MembershipRoleChangeError) as error:
        repository.change_role(
            tenant_id="tenant-a",
            actor_issuer="https://issuer.example/auth/v1",
            actor_subject="admin-subject",
            payload=_payload(),
        )

    assert error.value.code == expected_code
    sql = "\n".join(statement for statement, _params in cursor.executions)
    assert 'UPDATE "realytics".tenant_memberships' not in sql
    assert "tenant_identity_access_events" not in sql
