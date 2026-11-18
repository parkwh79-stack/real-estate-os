from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from db.repositories.default_workspace_repository import (
    DefaultWorkspaceError, DefaultWorkspaceUnavailable,
    PostgresDefaultWorkspaceRepository, default_workspace_id,
)

ISSUER = "https://test.supabase.co/auth/v1"
SUBJECT = "verified-subject"
NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


class _Database:
    def __init__(self):
        self.principals = {}
        self.tenants = {}
        self.memberships = []
        self.events = []
        self.executions = []
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_membership = False

    @contextmanager
    def transaction(self, issuer, subject):
        self.calls.append((issuer, subject))
        snapshot = copy.deepcopy((self.principals, self.tenants, self.memberships, self.events))
        try:
            yield _Connection(self)
        except Exception:
            self.principals, self.tenants, self.memberships, self.events = snapshot
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def repository(self):
        return PostgresDefaultWorkspaceRepository(
            transaction_factory=self.transaction, schema_name="realytics",
            now=lambda: NOW, event_id_factory=lambda: "default-workspace-event",
        )


class _Connection:
    def __init__(self, database):
        self.database = database
        self.tenant = None

    def cursor(self):
        return _Cursor(self)


class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.one = None
        self.all = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, parameters):
        sql = " ".join(query.split())
        db = self.connection.database
        db.executions.append((sql, parameters))
        self.one, self.all = None, []
        if "pg_advisory_xact_lock" in sql:
            return
        if sql.startswith("SELECT principal_type"):
            self.one = db.principals.get(tuple(parameters))
        elif sql.startswith("SELECT membership.tenant_id"):
            self.all = [
                (m["tenantId"], m["role"], m["status"], db.tenants[m["tenantId"]]["status"])
                for m in db.memberships if (m["issuer"], m["subject"]) == tuple(parameters)
            ]
        elif sql.startswith("SELECT tenant_id, status"):
            tenant = db.tenants.get(parameters[0])
            self.one = (parameters[0], tenant["status"]) if tenant else None
        elif sql.startswith('INSERT INTO "realytics".principals'):
            db.principals[tuple(parameters[:2])] = ("human", "active")
        elif sql.startswith('INSERT INTO "realytics".tenants'):
            db.tenants[parameters[0]] = {
                "name": parameters[1], "status": "active", "metadata": json.loads(parameters[4]),
            }
        elif sql.startswith('INSERT INTO "realytics".tenant_memberships'):
            if db.fail_membership:
                raise RuntimeError("synthetic write failure containing no credentials")
            db.memberships.append({
                "tenantId": parameters[0], "issuer": parameters[1], "subject": parameters[2],
                "role": "analyst", "status": "active",
            })
        elif sql.startswith("SELECT set_config"):
            self.connection.tenant = parameters[0]
        elif sql.startswith('INSERT INTO "realytics".tenant_identity_access_events'):
            assert self.connection.tenant == parameters[0]
            db.events.append(parameters)
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all


def _ensure(db, *, issuer=ISSUER, subject=SUBJECT):
    return db.repository().ensure_for_principal(
        issuer=issuer, subject=subject, email="profile@example.com", display_name="Profile Display",
    )


def _add_membership(db, tenant_id, *, role="viewer", status="active", tenant_status="active"):
    db.principals[(ISSUER, SUBJECT)] = ("human", "active")
    db.tenants[tenant_id] = {"name": "Existing institution", "status": tenant_status}
    db.memberships.append({"tenantId": tenant_id, "issuer": ISSUER, "subject": SUBJECT, "role": role, "status": status})


def test_first_bootstrap_is_atomic_and_repeated_login_reopens_the_same_workspace():
    db = _Database()
    first = _ensure(db, issuer=ISSUER + "///")
    second = _ensure(db)
    assert first == {"tenantId": default_workspace_id(ISSUER, SUBJECT), "role": "analyst", "created": True}
    assert second == {**first, "created": False}
    assert db.calls == [(ISSUER, SUBJECT), (ISSUER, SUBJECT)]
    assert len(db.principals) == len(db.tenants) == len(db.memberships) == len(db.events) == 1
    assert db.tenants[first["tenantId"]] == {
        "name": "내 작업공간", "status": "active",
        "metadata": {"workspaceType": "personal", "source": "verified_signup"},
    }
    assert all("profile@example.com" not in tenant["name"] for tenant in db.tenants.values())
    assert db.commits == 2
    sql = "\n".join(query for query, _params in db.executions)
    assert "tenant_access_requests" not in sql and "onboarding_profiles" not in sql
    assert not any(query.startswith(("UPDATE", "DELETE", "CREATE")) for query, _ in db.executions)


def test_different_verified_subjects_receive_different_workspaces_and_no_email_identity_key():
    db = _Database()
    first, second = _ensure(db), _ensure(db, subject="second-subject")
    assert first["tenantId"] != second["tenantId"]
    assert len(db.tenants) == len(db.memberships) == len(db.events) == 2
    assert default_workspace_id("a", "bc") != default_workspace_id("ab", "c")


def test_one_existing_active_membership_is_reused_with_its_original_role():
    db = _Database()
    _add_membership(db, "existing-org", role="viewer")
    result = _ensure(db)
    assert result == {"tenantId": "existing-org", "role": "viewer", "created": False}
    assert not db.events
    assert not any(query.startswith("INSERT") for query, _ in db.executions)


def test_multiple_active_memberships_keep_existing_tenant_selection():
    db = _Database()
    _add_membership(db, "org-a")
    _add_membership(db, "org-b")
    with pytest.raises(DefaultWorkspaceError, match="tenant_selection_required"):
        _ensure(db)
    assert not any(query.startswith("INSERT") for query, _ in db.executions)


@pytest.mark.parametrize("principal", [("human", "disabled"), ("human", "revoked"), ("service", "active"), ("unknown", "active")])
def test_disabled_or_nonhuman_directory_record_is_never_reactivated(principal):
    db = _Database()
    db.principals[(ISSUER, SUBJECT)] = principal
    with pytest.raises(DefaultWorkspaceError):
        _ensure(db)
    assert db.principals[(ISSUER, SUBJECT)] == principal
    assert not db.memberships and not db.tenants


@pytest.mark.parametrize("status", ["inactive", "suspended", "revoked", "pending"])
def test_existing_membership_removal_is_not_bypassed_by_a_fresh_default_workspace(status):
    db = _Database()
    _add_membership(db, "existing-org", status=status)
    with pytest.raises(DefaultWorkspaceError, match="no_active_tenant_membership"):
        _ensure(db)
    assert len(db.tenants) == 1 and db.memberships[0]["status"] == status
    assert not db.events


def test_inactive_default_tenant_and_orphan_collision_stay_fail_closed():
    for status, code in [("inactive", "tenant_inactive"), ("active", "default_workspace_conflict")]:
        db = _Database()
        db.tenants[default_workspace_id(ISSUER, SUBJECT)] = {"name": "Do not overwrite", "status": status}
        with pytest.raises(DefaultWorkspaceError, match=code):
            _ensure(db)
        assert not db.principals and not db.memberships and not db.events


def test_failed_membership_insert_rolls_back_principal_tenant_and_audit():
    db = _Database()
    db.fail_membership = True
    with pytest.raises(DefaultWorkspaceUnavailable, match="default workspace authority is unavailable"):
        _ensure(db)
    assert not db.principals and not db.tenants and not db.memberships and not db.events
    assert db.rollbacks == 1 and db.commits == 0


def test_unknown_existing_role_does_not_gain_analyst_permissions():
    db = _Database()
    _add_membership(db, "existing-org", role="unknown-privilege")
    with pytest.raises(DefaultWorkspaceError, match="invalid_membership_role"):
        _ensure(db)
    assert db.memberships[0]["role"] == "unknown-privilege" and not db.events


def test_revoked_personal_membership_is_not_restored_when_another_org_is_active():
    db = _Database()
    _add_membership(db, default_workspace_id(ISSUER, SUBJECT), status="revoked")
    _add_membership(db, "existing-active-org", role="viewer")
    result = _ensure(db)
    assert result == {"tenantId": "existing-active-org", "role": "viewer", "created": False}
    assert db.memberships[0]["status"] == "revoked"
    assert not db.events and len(db.tenants) == 2


def test_personal_id_cannot_be_submitted_through_the_organization_request_endpoint():
    from db.repositories.onboarding_repository import OnboardingLifecycleError, PostgresOnboardingRepository
    from schemas.onboarding import CreateTenantAccessRequestInput

    db = _Database()
    repository = PostgresOnboardingRepository(
        self_transaction_factory=db.transaction, review_transaction_factory=db.transaction,
    )
    with pytest.raises(OnboardingLifecycleError, match="organization_not_found"):
        repository.create_request(
            issuer=ISSUER, subject="someone-else",
            payload=CreateTenantAccessRequestInput(
                requestType="existing_organization",
                organizationId=default_workspace_id(ISSUER, SUBJECT),
            ),
        )
    assert db.calls == [] and not db.events
