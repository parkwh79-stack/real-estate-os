from __future__ import annotations

import pytest

from db.postgres_identity_runtime_preflight import (
    IdentityRuntimePreflightCode,
    IdentityRuntimePreflightError,
    assert_identity_runtime_connection,
    inspect_identity_runtime_connection,
)


ROLE = "realytics_identity"


class _Cursor:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection
        self.one = None
        self.all: list[tuple[object, ...]] = []

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False

    def execute(self, query: str, params=None) -> None:
        normalized = " ".join(query.split())
        self.one = None
        self.all = []
        if normalized == "SELECT current_user, session_user":
            self.one = (
                self.connection.current_role,
                self.connection.session_role,
            )
        elif "FROM pg_roles" in normalized and "rolcanlogin" in normalized:
            self.one = self.connection.role_state
        elif "FROM pg_auth_members" in normalized:
            self.all = [(item,) for item in self.connection.parent_roles]
        elif normalized.startswith("SELECT has_schema_privilege"):
            _role, schema = params
            privilege = "USAGE" if "'USAGE'" in normalized else "CREATE"
            self.one = ((str(schema), privilege) in self.connection.schema_grants,)
        elif "AS identity_owned_objects" in normalized:
            self.all = [(item,) for item in self.connection.owned_objects]
        else:
            raise AssertionError(normalized)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return list(self.all)


class _Connection:
    def __init__(
        self,
        *,
        current_role: str = ROLE,
        session_role: str = ROLE,
        role_state: tuple[object, ...] | None = (
            True,
            False,
            False,
            False,
            False,
            False,
            False,
        ),
    ) -> None:
        self.current_role = current_role
        self.session_role = session_role
        self.role_state = role_state
        self.parent_roles: tuple[str, ...] = ()
        self.schema_grants = {
            ("realytics", "USAGE"),
            ("extensions", "USAGE"),
        }
        self.owned_objects: tuple[str, ...] = ()

    def cursor(self) -> _Cursor:
        return _Cursor(self)


def _codes(issues) -> set[IdentityRuntimePreflightCode]:
    return {issue.code for issue in issues}


def test_exact_minimum_identity_login_passes() -> None:
    assert_identity_runtime_connection(_Connection(), expected_role=ROLE)


def test_identity_login_rejects_admin_dsn_and_bypassrls() -> None:
    connection = _Connection(
        current_role="postgres",
        session_role="postgres",
        role_state=(True, False, False, False, False, False, True),
    )

    issues = inspect_identity_runtime_connection(
        connection,
        expected_role=ROLE,
    )

    assert _codes(issues) >= {
        IdentityRuntimePreflightCode.WRONG_LOGIN_ROLE,
        IdentityRuntimePreflightCode.UNSAFE_ROLE_ATTRIBUTES,
    }
    assert "BYPASSRLS" in next(
        issue.objects
        for issue in issues
        if issue.code is IdentityRuntimePreflightCode.UNSAFE_ROLE_ATTRIBUTES
    )


def test_identity_login_rejects_inherit_create_and_owned_objects() -> None:
    connection = _Connection(
        role_state=(True, False, False, False, True, False, False),
    )
    connection.parent_roles = ("authenticated",)
    connection.schema_grants.add(("realytics", "CREATE"))
    connection.owned_objects = (
        "database:postgres",
        "relation:realytics.onboarding_profiles",
    )

    issues = inspect_identity_runtime_connection(
        connection,
        expected_role=ROLE,
    )

    assert _codes(issues) >= {
        IdentityRuntimePreflightCode.UNSAFE_ROLE_ATTRIBUTES,
        IdentityRuntimePreflightCode.INHERITED_ROLE_MEMBERSHIP,
        IdentityRuntimePreflightCode.FORBIDDEN_SCHEMA_CREATE,
        IdentityRuntimePreflightCode.FORBIDDEN_OWNERSHIP,
    }


def test_identity_preflight_error_is_secret_safe() -> None:
    with pytest.raises(IdentityRuntimePreflightError) as captured:
        assert_identity_runtime_connection(
            _Connection(current_role="postgres", session_role="postgres"),
            expected_role=ROLE,
        )

    assert "wrong_identity_login_role" in str(captured.value)
    assert "postgresql://" not in str(captured.value)
