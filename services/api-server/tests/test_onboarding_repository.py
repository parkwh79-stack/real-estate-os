from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from db.repositories.onboarding_repository import (
    OnboardingLifecycleError,
    PostgresOnboardingRepository,
    _profile_snapshot_hash,
)
from schemas.onboarding import (
    CreateTenantAccessRequestInput,
    DecideTenantAccessRequestInput,
    SaveOnboardingProfileInput,
)


NOW = datetime(2026, 7, 30, 5, 0, tzinfo=timezone.utc)
ISSUER = "https://project.supabase.co/auth/v1"


class _Cursor:
    def __init__(self, handler):
        self.handler = handler
        self.executions: list[tuple[str, tuple | None]] = []
        self.one = None
        self.all = []
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.executions.append((normalized, params))
        result = self.handler(normalized, params, self)
        result = result or {}
        self.one = result.get("one")
        self.all = result.get("all", [])
        self.rowcount = result.get("rowcount", -1)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all


class _Connection:
    def __init__(self, cursor: _Cursor):
        self.cursor_object = cursor

    def cursor(self):
        return self.cursor_object


class _Factory:
    def __init__(self, connection: _Connection, *, fail: bool = False):
        self.connection = connection
        self.fail = fail
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def __call__(self, issuer: str, subject: str):
        self.calls.append((issuer, subject))
        if self.fail:
            raise AssertionError("unexpected transaction pool")
        self.active += 1
        try:
            yield self.connection
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1
        finally:
            self.active -= 1


def _profile_row(version: int = 1):
    return (
        ISSUER,
        "signup-subject",
        "signup@example.com",
        "Signup User",
        None,
        "Analyst",
        "realytics-terms-2026-07-30",
        "realytics-privacy-2026-07-30",
        NOW,
        NOW,
        version,
        NOW,
    )


def _request_row(
    *,
    request_type: str = "existing_organization",
    requester_subject: str = "signup-subject",
    organization_id: str | None = "tenant-a",
    organization_name: str = "Acme Capital",
    status: str = "pending",
    version: int = 1,
    approved_tenant_id: str | None = None,
    snapshot_hash: str | None = None,
):
    profile = {
        "contactEmail": "signup@example.com",
        "displayName": "Signup User",
        "profileVersion": 1,
        "termsVersion": "realytics-terms-2026-07-30",
        "privacyVersion": "realytics-privacy-2026-07-30",
    }
    return (
        "access-request-1",
        request_type,
        ISSUER,
        requester_subject,
        "Signup User",
        "signup@example.com",
        1,
        "realytics-terms-2026-07-30",
        "realytics-privacy-2026-07-30",
        snapshot_hash
        or _profile_snapshot_hash(
            issuer=ISSUER,
            subject=requester_subject,
            profile=profile,
        ),
        organization_id,
        organization_name,
        "1234567890" if request_type == "new_organization" else None,
        "analyst",
        "Please review",
        status,
        version,
        NOW,
        None if status == "pending" else NOW,
        None if status == "pending" else "Approved for pilot",
        approved_tenant_id,
        NOW,
        NOW,
    )


def _profile_input(expected_version: int = 0):
    return SaveOnboardingProfileInput(
        displayName="Signup User",
        jobTitle="Analyst",
        termsAccepted=True,
        privacyAccepted=True,
        termsVersion="realytics-terms-2026-07-30",
        privacyVersion="realytics-privacy-2026-07-30",
        expectedProfileVersion=expected_version,
    )


def test_memberless_status_uses_identity_pool_when_api_pool_is_unavailable() -> None:
    def handler(sql, _params, _cursor):
        if "FROM \"realytics\".onboarding_profiles" in sql:
            return {"one": None}
        if "FROM \"realytics\".tenant_access_requests AS request" in sql:
            return {"all": []}
        if "FROM \"realytics\".tenant_memberships AS membership" in sql:
            return {"all": []}
        if "FROM \"realytics\".platform_onboarding_operators" in sql:
            return {"one": None}
        raise AssertionError(sql)

    cursor = _Cursor(handler)
    self_factory = _Factory(_Connection(cursor))
    review_factory = _Factory(_Connection(_Cursor(handler)), fail=True)
    repository = PostgresOnboardingRepository(
        self_transaction_factory=self_factory,
        review_transaction_factory=review_factory,
    )

    status = repository.get_status(
        issuer=f" {ISSUER}/ ",
        subject=" signup-subject ",
    )

    assert status["nextAction"] == "complete_profile"
    assert status["memberships"] == []
    assert self_factory.calls == [(ISSUER, "signup-subject")]
    assert review_factory.calls == []
    assert self_factory.commits == 1


def test_profile_create_is_cas_guarded_and_appends_consent_event() -> None:
    load_count = 0

    def handler(sql, _params, _cursor):
        nonlocal load_count
        if "pg_advisory_xact_lock" in sql:
            return {}
        if "SELECT profile_version" in sql:
            return {"one": None}
        if "INSERT INTO \"realytics\".onboarding_profiles" in sql:
            return {"rowcount": 1}
        if "INSERT INTO \"realytics\".onboarding_principal_events" in sql:
            return {"rowcount": 1}
        if "FROM \"realytics\".onboarding_profiles" in sql:
            load_count += 1
            return {"one": _profile_row()}
        raise AssertionError(sql)

    cursor = _Cursor(handler)
    self_factory = _Factory(_Connection(cursor))
    repository = PostgresOnboardingRepository(
        self_transaction_factory=self_factory,
        review_transaction_factory=_Factory(_Connection(cursor), fail=True),
        now=lambda: NOW,
        event_id_factory=lambda: "onboarding-event-1",
    )

    profile = repository.save_profile(
        issuer=ISSUER,
        subject="signup-subject",
        contact_email="signup@example.com",
        payload=_profile_input(),
    )

    assert profile["profileVersion"] == 1
    assert load_count == 1
    event = next(
        item for item in cursor.executions
        if "onboarding_principal_events" in item[0]
    )
    assert "onboarding_profile_saved" in event[1]
    assert "realytics-terms-2026-07-30" in event[1][4]
    assert self_factory.commits == 1


def test_profile_cas_conflict_rolls_back_before_write() -> None:
    def handler(sql, _params, _cursor):
        if "pg_advisory_xact_lock" in sql:
            return {}
        if "SELECT profile_version" in sql:
            return {"one": (3,)}
        raise AssertionError(sql)

    cursor = _Cursor(handler)
    factory = _Factory(_Connection(cursor))
    repository = PostgresOnboardingRepository(
        self_transaction_factory=factory,
        review_transaction_factory=_Factory(_Connection(cursor), fail=True),
        now=lambda: NOW,
    )

    with pytest.raises(
        OnboardingLifecycleError,
        match="onboarding_profile_version_conflict",
    ):
        repository.save_profile(
            issuer=ISSUER,
            subject="signup-subject",
            contact_email="signup@example.com",
            payload=_profile_input(expected_version=2),
        )

    assert factory.rollbacks == 1
    assert not any("INSERT INTO" in sql for sql, _ in cursor.executions)


def test_request_submission_seals_current_profile_snapshot() -> None:
    expected_hash = _profile_snapshot_hash(
        issuer=ISSUER,
        subject="signup-subject",
        profile={
            "contactEmail": "signup@example.com",
            "displayName": "Signup User",
            "profileVersion": 1,
            "termsVersion": "realytics-terms-2026-07-30",
            "privacyVersion": "realytics-privacy-2026-07-30",
        },
    )

    def handler(sql, _params, _cursor):
        if "FROM \"realytics\".onboarding_profiles" in sql:
            return {"one": _profile_row()}
        if "pg_advisory_xact_lock" in sql:
            return {}
        if "SELECT status FROM \"realytics\".tenants" in sql:
            return {"one": ("active",)}
        if "FROM \"realytics\".tenant_memberships" in sql:
            return {"one": None}
        if (
            "FROM \"realytics\".tenant_access_requests" in sql
            and " AS request" not in sql
        ):
            return {"one": None}
        if sql.startswith("INSERT INTO"):
            return {"rowcount": 1}
        if "tenant_access_requests AS request" in sql:
            return {"one": _request_row(snapshot_hash=expected_hash)}
        raise AssertionError(sql)

    cursor = _Cursor(handler)
    factory = _Factory(_Connection(cursor))
    repository = PostgresOnboardingRepository(
        self_transaction_factory=factory,
        review_transaction_factory=_Factory(_Connection(cursor), fail=True),
        now=lambda: NOW,
        request_id_factory=lambda: "access-request-1",
        event_id_factory=lambda: "event-1",
    )

    created = repository.create_request(
        issuer=ISSUER,
        subject="signup-subject",
        payload=CreateTenantAccessRequestInput(
            requestType="existing_organization",
            organizationId="tenant-a",
        ),
    )

    request_insert = next(
        params
        for sql, params in cursor.executions
        if sql.startswith("INSERT INTO \"realytics\".tenant_access_requests")
    )
    assert request_insert[4:10] == (
        "signup@example.com",
        "Signup User",
        1,
        "realytics-terms-2026-07-30",
        "realytics-privacy-2026-07-30",
        expected_hash,
    )
    assert created["profileSnapshotHash"] == expected_hash
    assert factory.commits == 1


def test_review_list_resolves_single_admin_tenant_without_jwt_role() -> None:
    def handler(sql, _params, _cursor):
        if "FROM \"realytics\".platform_onboarding_operators" in sql:
            return {"one": None}
        if (
            "FROM \"realytics\".tenant_memberships" in sql
            and "LIMIT 2" in sql
        ):
            return {"all": [("tenant-a",)]}
        if "tenant_access_requests AS request" in sql:
            return {"all": [_request_row()]}
        raise AssertionError(sql)

    cursor = _Cursor(handler)
    review_factory = _Factory(_Connection(cursor))
    repository = PostgresOnboardingRepository(
        self_transaction_factory=_Factory(_Connection(cursor), fail=True),
        review_transaction_factory=review_factory,
    )

    scope, requests = repository.list_reviewable(
        actor_issuer=ISSUER,
        actor_subject="admin-subject",
        tenant_id=None,
        status="all",
    )

    assert scope == "tenant"
    assert requests[0]["organizationId"] == "tenant-a"
    final_params = cursor.executions[-1][1]
    assert final_params == ("tenant-a", 100)


def test_existing_org_approval_is_one_api_transaction_with_db_admin_check() -> None:
    request_reads = 0

    def handler(sql, _params, _cursor):
        nonlocal request_reads
        if "pg_advisory_xact_lock" in sql:
            return {}
        if "tenant_access_requests AS request" in sql:
            request_reads += 1
            if request_reads == 1:
                return {"one": _request_row()}
            return {
                "one": _request_row(
                    status="approved",
                    version=2,
                    approved_tenant_id="tenant-a",
                )
            }
        if "role = 'admin' AND status = 'active'" in sql:
            return {"one": (1,)}
        if "FROM \"realytics\".onboarding_profiles" in sql:
            return {"one": _profile_row()}
        if "SELECT status FROM \"realytics\".tenants" in sql:
            return {"one": ("active",)}
        if "SELECT principal_type, status" in sql:
            return {"one": None}
        if "SELECT status FROM \"realytics\".tenant_memberships" in sql:
            return {"one": None}
        if sql.startswith("UPDATE \"realytics\".tenant_access_requests"):
            return {"rowcount": 1}
        if sql.startswith("INSERT INTO"):
            return {"rowcount": 1}
        raise AssertionError(sql)

    cursor = _Cursor(handler)
    review_factory = _Factory(_Connection(cursor))
    repository = PostgresOnboardingRepository(
        self_transaction_factory=_Factory(_Connection(cursor), fail=True),
        review_transaction_factory=review_factory,
        now=lambda: NOW,
        event_id_factory=iter(
            [f"event-{index}" for index in range(1, 10)]
        ).__next__,
    )
    payload = DecideTenantAccessRequestInput(
        decision="approve",
        expectedRequestVersion=1,
        assignedRole="viewer",
        reason="Approved for pilot",
    )

    decided = repository.decide_request(
        actor_issuer=ISSUER,
        actor_subject="admin-subject",
        request_id="access-request-1",
        payload=payload,
    )

    assert decided["status"] == "approved"
    assert review_factory.calls == [(ISSUER, "admin-subject")]
    assert review_factory.commits == 1
    assert review_factory.rollbacks == 0
    sql = "\n".join(item[0] for item in cursor.executions)
    assert "role = 'admin' AND status = 'active'" in sql
    assert 'INSERT INTO "realytics".principals' in sql
    assert 'INSERT INTO "realytics".tenant_memberships' in sql
    assert 'UPDATE "realytics".tenant_access_requests' in sql
    assert sql.count('INSERT INTO "realytics".tenant_access_request_events') == 3
    assert 'FROM "realytics".onboarding_profiles' not in sql
    decision_index = next(
        index
        for index, (statement, _params) in enumerate(cursor.executions)
        if statement.startswith('UPDATE "realytics".tenant_access_requests')
    )
    event_indexes = [
        index
        for index, (statement, _params) in enumerate(cursor.executions)
        if statement.startswith(
            'INSERT INTO "realytics".tenant_access_request_events'
        )
    ]
    assert event_indexes and min(event_indexes) > decision_index


def test_new_org_approval_requires_operator_and_creates_tenant_atomically() -> None:
    request_reads = 0

    def handler(sql, _params, _cursor):
        nonlocal request_reads
        if "pg_advisory_xact_lock" in sql:
            return {}
        if "tenant_access_requests AS request" in sql:
            request_reads += 1
            if request_reads == 1:
                return {
                    "one": _request_row(
                        request_type="new_organization",
                        organization_id=None,
                    )
                }
            return {
                "one": _request_row(
                    request_type="new_organization",
                    organization_id=None,
                    status="approved",
                    version=2,
                    approved_tenant_id="tenant-new",
                )
            }
        if "FROM \"realytics\".platform_onboarding_operators" in sql:
            return {"one": (1,)}
        if "FROM \"realytics\".onboarding_profiles" in sql:
            return {"one": _profile_row()}
        if "lower(btrim(name))" in sql:
            return {"one": None}
        if "SELECT principal_type, status" in sql:
            return {"one": None}
        if "SELECT status FROM \"realytics\".tenant_memberships" in sql:
            return {"one": None}
        if sql.startswith("UPDATE \"realytics\".tenant_access_requests"):
            return {"rowcount": 1}
        if sql.startswith("INSERT INTO"):
            return {"rowcount": 1}
        raise AssertionError(sql)

    cursor = _Cursor(handler)
    review_factory = _Factory(_Connection(cursor))
    repository = PostgresOnboardingRepository(
        self_transaction_factory=_Factory(_Connection(cursor), fail=True),
        review_transaction_factory=review_factory,
        now=lambda: NOW,
        tenant_id_factory=lambda: "tenant-new",
        event_id_factory=iter(
            [f"event-{index}" for index in range(1, 12)]
        ).__next__,
    )

    result = repository.decide_request(
        actor_issuer=ISSUER,
        actor_subject="operator-subject",
        request_id="access-request-1",
        payload=DecideTenantAccessRequestInput(
            decision="approve",
            expectedRequestVersion=1,
            assignedRole="admin",
            reason="Approved new organization",
        ),
    )

    assert result["approvedOrganizationId"] == "tenant-new"
    assert review_factory.commits == 1
    sql = "\n".join(item[0] for item in cursor.executions)
    assert 'platform_onboarding_operators' in sql
    assert 'INSERT INTO "realytics".tenants' in sql
    assert 'INSERT INTO "realytics".tenant_memberships' in sql
    assert sql.count('INSERT INTO "realytics".tenant_access_request_events') == 4


def test_approval_rejects_tampered_profile_snapshot_before_any_write() -> None:
    def handler(sql, _params, _cursor):
        if "pg_advisory_xact_lock" in sql:
            return {}
        if "tenant_access_requests AS request" in sql:
            return {"one": _request_row(snapshot_hash="0" * 64)}
        if "role = 'admin' AND status = 'active'" in sql:
            return {"one": (1,)}
        raise AssertionError(sql)

    cursor = _Cursor(handler)
    factory = _Factory(_Connection(cursor))
    repository = PostgresOnboardingRepository(
        self_transaction_factory=_Factory(_Connection(cursor), fail=True),
        review_transaction_factory=factory,
        now=lambda: NOW,
    )

    with pytest.raises(
        OnboardingLifecycleError,
        match="onboarding_profile_snapshot_integrity_error",
    ):
        repository.decide_request(
            actor_issuer=ISSUER,
            actor_subject="admin-subject",
            request_id="access-request-1",
            payload=DecideTenantAccessRequestInput(
                decision="approve",
                expectedRequestVersion=1,
                reason="Approved for pilot",
            ),
        )

    assert factory.rollbacks == 1
    assert not any(
        sql.startswith("INSERT INTO") or sql.startswith("UPDATE ")
        for sql, _params in cursor.executions
    )


def test_new_organization_approval_requires_explicit_initial_admin() -> None:
    def handler(sql, _params, _cursor):
        if "pg_advisory_xact_lock" in sql:
            return {}
        if "tenant_access_requests AS request" in sql:
            return {
                "one": _request_row(
                    request_type="new_organization",
                    organization_id=None,
                )
            }
        if "FROM \"realytics\".platform_onboarding_operators" in sql:
            return {"one": (1,)}
        raise AssertionError(sql)

    cursor = _Cursor(handler)
    factory = _Factory(_Connection(cursor))
    repository = PostgresOnboardingRepository(
        self_transaction_factory=_Factory(_Connection(cursor), fail=True),
        review_transaction_factory=factory,
        now=lambda: NOW,
    )

    with pytest.raises(
        OnboardingLifecycleError,
        match="new_organization_initial_admin_required",
    ):
        repository.decide_request(
            actor_issuer=ISSUER,
            actor_subject="operator-subject",
            request_id="access-request-1",
            payload=DecideTenantAccessRequestInput(
                decision="approve",
                expectedRequestVersion=1,
                reason="Approved new organization",
            ),
        )

    assert factory.rollbacks == 1


def test_decision_forbids_self_approval_and_rolls_back() -> None:
    def handler(sql, _params, _cursor):
        if "pg_advisory_xact_lock" in sql:
            return {}
        if "tenant_access_requests AS request" in sql:
            return {"one": _request_row(requester_subject="admin-subject")}
        raise AssertionError(sql)

    cursor = _Cursor(handler)
    factory = _Factory(_Connection(cursor))
    repository = PostgresOnboardingRepository(
        self_transaction_factory=_Factory(_Connection(cursor), fail=True),
        review_transaction_factory=factory,
        now=lambda: NOW,
    )

    with pytest.raises(
        OnboardingLifecycleError,
        match="tenant_access_request_self_approval_forbidden",
    ):
        repository.decide_request(
            actor_issuer=ISSUER,
            actor_subject="admin-subject",
            request_id="access-request-1",
            payload=DecideTenantAccessRequestInput(
                decision="approve",
                expectedRequestVersion=1,
                reason="Should be rejected",
            ),
        )

    assert factory.rollbacks == 1
    assert not any("INSERT INTO" in sql for sql, _ in cursor.executions)
