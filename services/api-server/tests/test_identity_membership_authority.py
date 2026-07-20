from __future__ import annotations

from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from core import authz, idp
from core.runtime_profile import (
    identity_membership_authority,
    validate_security_configuration,
)
from db.repositories.identity_membership_repository import (
    IdentityPrincipalRecord,
    PostgresIdentityMembershipRepository,
    PrincipalMembershipDirectory,
    TenantMembershipRecord,
)


ISSUER = "https://tenant-auth.example/issuer"
SUBJECT = "verified-subject-123"


class _DirectoryRepository:
    def __init__(self, directory: PrincipalMembershipDirectory) -> None:
        self.directory = directory
        self.calls: list[tuple[str, str]] = []

    def load_directory(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> PrincipalMembershipDirectory:
        self.calls.append((issuer, subject))
        return self.directory


def _principal(
    *,
    token_tenant: str | None = None,
    token_role: str | None = None,
) -> idp.IdpPrincipal:
    return idp.IdpPrincipal(
        user_id=SUBJECT,
        issuer=ISSUER,
        subject=SUBJECT,
        tenant_id=token_tenant,
        email="signed-profile@example.com",
        display_name="Signed Profile",
        role=token_role,
        provider="supabase",
        principal_type="human",
        claims={
            "iss": ISSUER,
            "sub": SUBJECT,
            "app_metadata": {
                "realytics_tenant_id": token_tenant,
                "realytics_role": token_role,
            },
        },
        token_id="jwt-1",
    )


def _directory(
    *memberships: TenantMembershipRecord,
    principal_status: str = "active",
    principal_type: str = "human",
) -> PrincipalMembershipDirectory:
    return PrincipalMembershipDirectory(
        principal=IdentityPrincipalRecord(
            issuer=ISSUER,
            subject=SUBJECT,
            principal_type=principal_type,
            status=principal_status,
            email="directory@example.com",
            display_name="Directory Principal",
            legacy_user_id="legacy-directory-user",
        ),
        memberships=tuple(memberships),
    )


def _membership(
    tenant_id: str,
    *,
    role: str = "viewer",
    status: str = "active",
    version: int = 1,
) -> TenantMembershipRecord:
    return TenantMembershipRecord(
        tenant_id=tenant_id,
        role=role,
        status=status,
        membership_version=version,
    )


def _request(
    *,
    tenant_header: str | None = None,
    tenant_query: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if tenant_header is not None:
        headers.append(
            (b"x-realytics-tenant-id", tenant_header.encode("utf-8"))
        )
    query = (
        urlencode({"tenant_id": tenant_query}).encode("utf-8")
        if tenant_query is not None
        else b""
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/auth/me",
            "headers": headers,
            "query_string": query,
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


def _install_authority(
    monkeypatch,
    directory: PrincipalMembershipDirectory,
    *,
    principal: idp.IdpPrincipal | None = None,
) -> _DirectoryRepository:
    repository = _DirectoryRepository(directory)
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY",
        "postgres",
    )
    monkeypatch.setattr(
        authz,
        "principal_from_request",
        lambda request: principal or _principal(),
    )
    monkeypatch.setattr(
        authz,
        "identity_membership_repository",
        lambda: repository,
    )
    monkeypatch.setattr(authz, "upsert_user", lambda payload: dict(payload))
    return repository


def test_postgres_authority_ignores_malicious_app_metadata_and_uses_db_role(
    monkeypatch,
):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY",
        "postgres",
    )
    parsed = idp._principal_from_claims(
        {
            "iss": ISSUER + "///",
            "sub": SUBJECT,
            "email": "attacker@example.com",
            "app_metadata": {
                "realytics_tenant_id": "attacker-tenant",
                "realytics_role": "admin",
                "realytics_principal_type": "service",
            },
        }
    )
    assert parsed.issuer == ISSUER
    assert parsed.subject == SUBJECT
    assert parsed.user_id == SUBJECT
    assert parsed.tenant_id is None
    assert parsed.role is None

    repository = _install_authority(
        monkeypatch,
        _directory(_membership("governed-tenant", role="viewer", version=7)),
        principal=parsed,
    )
    context = authz.identity_context_from_request(
        _request(tenant_header="governed-tenant")
    )

    assert repository.calls == [(ISSUER, SUBJECT)]
    assert context["user"]["tenantId"] == "governed-tenant"
    assert context["user"]["role"] == "viewer"
    assert context["user"]["metadata"]["principalType"] == "human"
    assert context["membershipAuthority"] == {
        "source": "normalized_postgres",
        "principalStatus": "active",
        "membershipStatus": "active",
        "membershipVersion": 7,
    }
    assert "user:manage" not in context["permissions"]


def test_one_active_membership_is_selected_without_tenant_selector(monkeypatch):
    _install_authority(
        monkeypatch,
        _directory(
            _membership("inactive-tenant", status="suspended"),
            _membership("only-active", role="analyst", version=3),
        ),
    )

    context = authz.identity_context_from_request(_request())

    assert context["user"]["tenantId"] == "only-active"
    assert context["role"] == "analyst"
    assert context["membershipAuthority"]["membershipVersion"] == 3


def test_postgres_authority_accepts_verified_jwt_without_tenant_claim(
    monkeypatch,
):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY",
        "postgres",
    )

    principal = idp._principal_from_claims(
        {"iss": ISSUER + "/", "sub": SUBJECT}
    )

    assert principal.issuer == ISSUER
    assert principal.subject == SUBJECT
    assert principal.tenant_id is None
    assert principal.role is None


def test_multiple_active_memberships_require_explicit_tenant_selection(
    monkeypatch,
):
    _install_authority(
        monkeypatch,
        _directory(
            _membership("tenant-a"),
            _membership("tenant-b", role="reviewer"),
        ),
    )

    with pytest.raises(HTTPException) as error:
        authz.identity_context_from_request(_request())

    assert error.value.status_code == 400
    assert error.value.detail == {
        "error": "tenant_selection_required",
        "activeMembershipCount": 2,
    }


def test_no_active_membership_fails_closed(monkeypatch):
    _install_authority(
        monkeypatch,
        _directory(
            _membership("tenant-a", status="suspended"),
            _membership("tenant-b", status="revoked"),
        ),
    )

    with pytest.raises(HTTPException) as error:
        authz.identity_context_from_request(_request())

    assert error.value.status_code == 403
    assert error.value.detail == {"error": "no_active_tenant_membership"}


def test_requested_tenant_must_match_an_active_db_membership(monkeypatch):
    _install_authority(
        monkeypatch,
        _directory(
            _membership("tenant-a", role="reviewer"),
            _membership("tenant-disabled", status="inactive"),
        ),
    )

    with pytest.raises(HTTPException) as missing:
        authz.identity_context_from_request(
            _request(tenant_header="attacker-tenant")
        )
    assert missing.value.status_code == 403
    assert missing.value.detail["error"] == "tenant_membership_not_found"

    with pytest.raises(HTTPException) as inactive:
        authz.identity_context_from_request(
            _request(tenant_header="tenant-disabled")
        )
    assert inactive.value.status_code == 403
    assert inactive.value.detail["error"] == "tenant_membership_inactive"


def test_disabled_db_principal_is_rejected_before_membership_role(monkeypatch):
    _install_authority(
        monkeypatch,
        _directory(
            _membership("tenant-a", role="admin"),
            principal_status="disabled",
        ),
    )

    with pytest.raises(HTTPException) as error:
        authz.identity_context_from_request(
            _request(tenant_header="tenant-a")
        )

    assert error.value.status_code == 403
    assert error.value.detail == {
        "error": "principal_disabled",
        "principalStatus": "disabled",
    }


def test_conflicting_tenant_selectors_fail_closed(monkeypatch):
    _install_authority(
        monkeypatch,
        _directory(_membership("tenant-a")),
    )

    with pytest.raises(HTTPException) as error:
        authz.identity_context_from_request(
            _request(tenant_header="tenant-a", tenant_query="tenant-b")
        )

    assert error.value.status_code == 400
    assert error.value.detail["error"] == "tenant_selector_conflict"


def test_local_claims_compatibility_remains_explicit_and_deterministic(
    monkeypatch,
):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "development")
    monkeypatch.setenv(
        "REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY",
        "claims",
    )

    principal = idp._principal_from_claims(
        {
            "iss": ISSUER,
            "sub": SUBJECT,
            "tenant_id": "local-tenant",
            "role": "reviewer",
        }
    )

    assert identity_membership_authority() == "claims"
    assert principal.tenant_id == "local-tenant"
    assert principal.role == "reviewer"


def test_hardened_configuration_rejects_claims_membership_authority(
    monkeypatch,
):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY",
        "claims",
    )

    assert identity_membership_authority() == "postgres"
    with pytest.raises(RuntimeError) as error:
        validate_security_configuration()

    assert (
        "REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY=postgres"
        in str(error.value)
    )


class _Cursor:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.executions: list[tuple[str, tuple[str, str]]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql: str, params: tuple[str, str]) -> None:
        self.executions.append((sql, params))

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self) -> _Cursor:
        return self._cursor


def test_postgres_repository_uses_parameterized_read_only_query():
    cursor = _Cursor(
        [
            (
                ISSUER,
                SUBJECT,
                "human",
                "active",
                "directory@example.com",
                "Directory Principal",
                "legacy-user",
                "tenant-a",
                "legal_reviewer",
                "active",
                11,
            )
        ]
    )
    repository = PostgresIdentityMembershipRepository(
        transaction_factory=lambda: _Connection(cursor),
        schema_name="realytics",
    )

    result = repository.load_directory(
        issuer=ISSUER + "///",
        subject=f"  {SUBJECT}  ",
    )

    assert result.principal is not None
    assert result.principal.status == "active"
    assert result.memberships[0].membership_version == 11
    assert len(cursor.executions) == 1
    sql, params = cursor.executions[0]
    assert params == (ISSUER, SUBJECT)
    assert sql.lstrip().upper().startswith("SELECT")
    assert '"realytics".principals' in sql
    assert '"realytics".tenant_memberships' in sql
    assert "INSERT" not in sql.upper()
    assert "UPDATE" not in sql.upper()
    assert "CREATE" not in sql.upper()
