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
    tenant_status: str | None = "active",
    role: str = "viewer",
    status: str = "active",
    version: int = 1,
) -> TenantMembershipRecord:
    return TenantMembershipRecord(
        tenant_id=tenant_id,
        tenant_status=tenant_status,
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
    def legacy_projection_forbidden(*_args, **_kwargs):
        raise AssertionError("Normalized identity must not write a legacy user projection")
    monkeypatch.setattr(authz, "upsert_user", legacy_projection_forbidden)
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
        "tenantStatus": "active",
        "membershipStatus": "active",
        "membershipVersion": 7,
    }
    assert "user:manage" not in context["permissions"]


def test_request_scoped_identity_context_reads_membership_once_and_is_defensive(
    monkeypatch,
):
    repository = _install_authority(
        monkeypatch,
        _directory(_membership("tenant-a", role="analyst", version=9)),
    )
    request = _request(tenant_header="tenant-a")

    first = authz.identity_context_from_request(request)
    first["role"] = "admin"
    first["permissions"].append("user:manage")
    first["user"]["metadata"]["membershipAuthority"]["membershipVersion"] = 999
    first["authorization"] = {"allowed": True}

    second = authz.identity_context_from_request(request)

    assert repository.calls == [(ISSUER, SUBJECT)]
    assert second["role"] == "analyst"
    assert "user:manage" not in second["permissions"]
    assert (
        second["user"]["metadata"]["membershipAuthority"][
            "membershipVersion"
        ]
        == 9
    )
    assert "authorization" not in second


def test_request_identity_cache_does_not_cross_tenant_selector_or_request(
    monkeypatch,
):
    repository = _install_authority(
        monkeypatch,
        _directory(
            _membership("tenant-a", role="viewer", version=1),
            _membership("tenant-b", role="reviewer", version=2),
        ),
    )

    tenant_a_request = _request(tenant_header="tenant-a")
    tenant_b_request = _request(tenant_header="tenant-b")
    first_a = authz.identity_context_from_request(tenant_a_request)
    second_a = authz.identity_context_from_request(tenant_a_request)
    tenant_b = authz.identity_context_from_request(tenant_b_request)

    assert first_a["user"]["tenantId"] == "tenant-a"
    assert second_a["user"]["tenantId"] == "tenant-a"
    assert tenant_b["user"]["tenantId"] == "tenant-b"
    assert tenant_b["role"] == "reviewer"
    assert repository.calls == [(ISSUER, SUBJECT), (ISSUER, SUBJECT)]


def test_authorize_request_annotation_does_not_mutate_cached_base(monkeypatch):
    repository = _install_authority(
        monkeypatch,
        _directory(_membership("tenant-a", role="viewer", version=4)),
    )
    request = _request(tenant_header="tenant-a")

    authorized = authz.authorize_request(request, "project:read")
    cached_base = authz.identity_context_from_request(request)

    assert authorized["authorization"]["allowed"] is True
    assert "authorization" not in cached_base
    assert repository.calls == [(ISSUER, SUBJECT)]


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


def test_requested_tenant_must_be_active_even_when_membership_is_active(
    monkeypatch,
):
    _install_authority(
        monkeypatch,
        _directory(
            _membership(
                "tenant-suspended",
                tenant_status="suspended",
                role="admin",
            ),
        ),
    )

    with pytest.raises(HTTPException) as error:
        authz.identity_context_from_request(
            _request(tenant_header="tenant-suspended")
        )

    assert error.value.status_code == 403
    assert error.value.detail == {
        "error": "tenant_inactive",
        "requestedTenantId": "tenant-suspended",
        "tenantStatus": "suspended",
    }


def test_implicit_selection_fails_closed_when_tenant_status_is_missing(
    monkeypatch,
):
    _install_authority(
        monkeypatch,
        _directory(
            _membership("tenant-legacy", tenant_status=None, role="admin"),
        ),
    )

    with pytest.raises(HTTPException) as error:
        authz.identity_context_from_request(_request())

    assert error.value.status_code == 403
    assert error.value.detail == {
        "error": "tenant_inactive",
        "requestedTenantId": "tenant-legacy",
        "tenantStatus": "unknown",
    }


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
                "active",
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
    assert '"realytics".tenants' in sql
    assert "INSERT" not in sql.upper()
    assert "UPDATE" not in sql.upper()
    assert "CREATE" not in sql.upper()


def test_hardened_auth_me_never_loads_or_saves_legacy_state_and_rechecks_revocation(monkeypatch):
    from fastapi.testclient import TestClient
    import main as api_main
    import storage as legacy_storage
    from db import postgres as legacy_postgres
    from db.repositories.identity_membership_repository import IdentityMembershipRepositoryUnavailable

    repository = _install_authority(
        monkeypatch,
        _directory(_membership("governed-tenant", role="viewer", version=4)),
        principal=_principal(token_tenant="forged-tenant", token_role="admin"),
    )
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(api_main, "storage_backend", lambda: "postgres")
    legacy_calls = []

    def forbidden(*_args, **_kwargs):
        legacy_calls.append("forbidden_compatibility_access")
        raise AssertionError("Normalized auth must not access compatibility state or DDL")

    for owner, name in (
        (authz, "upsert_user"), (authz, "get_user"), (authz, "get_user_session"),
        (legacy_storage, "upsert_user"), (legacy_storage, "upsert_tenant"),
        (legacy_storage, "load_state"), (legacy_storage, "save_state"),
        (legacy_storage, "_load_json_state"), (legacy_storage, "_save_json_state"),
        (legacy_postgres, "load_state_document"),
        (legacy_postgres, "_ensure_app_state_table"),
        (api_main, "record_user_activity_log"),
    ):
        monkeypatch.setattr(owner, name, forbidden)

    client = TestClient(api_main.app)
    response = client.get("/auth/me", headers={"authorization": "Bearer verified-test-token"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["userId"] == "legacy-directory-user"
    assert body["user"]["tenantId"] == "governed-tenant"
    assert body["user"]["role"] == "viewer"
    assert body["user"]["metadata"]["membershipAuthority"]["membershipVersion"] == 4
    assert "user:manage" not in body["permissions"]
    assert legacy_calls == []

    repository.directory = _directory(_membership("governed-tenant", role="viewer", status="revoked", version=5))
    revoked = client.get("/auth/me", headers={"authorization": "Bearer verified-test-token"})
    assert revoked.status_code == 403
    assert revoked.json()["detail"]["error"] == "no_active_tenant_membership"
    assert repository.calls == [(ISSUER, SUBJECT), (ISSUER, SUBJECT)]

    def unavailable(**_kwargs):
        raise IdentityMembershipRepositoryUnavailable("synthetic directory outage")
    monkeypatch.setattr(repository, "load_directory", unavailable)
    outage = client.get("/auth/me", headers={"authorization": "Bearer verified-test-token"})
    assert outage.status_code == 503
    assert outage.json()["detail"]["error"] == "identity_membership_authority_unavailable"
    assert legacy_calls == []


def test_claims_compatibility_still_persists_its_user_projection(monkeypatch):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "development")
    monkeypatch.setenv("REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY", "claims")
    principal = _principal(token_tenant="local-tenant", token_role="analyst")
    monkeypatch.setattr(authz, "principal_from_request", lambda _request: principal)
    calls = []

    def upsert(payload, **kwargs):
        calls.append((dict(payload), kwargs))
        return {**payload, "role": "admin", "tenantId": "stale-tenant", "status": "disabled", "legacyDisplayMarker": "preserved"}

    monkeypatch.setattr(authz, "upsert_user", upsert)
    context = authz.identity_context_from_request(_request(tenant_header="local-tenant"))
    assert len(calls) == 1
    assert calls[0][0]["tenantId"] == "local-tenant"
    assert calls[0][1] == {"skip_unchanged": True}
    assert context["membershipAuthority"]["source"] == "verified_bearer_claims"
    assert context["user"]["legacyDisplayMarker"] == "preserved"
    assert context["user"]["tenantId"] == "local-tenant"
    assert context["user"]["role"] == "analyst"
    assert context["user"]["status"] == "active"
    assert "user:manage" not in context["permissions"]
