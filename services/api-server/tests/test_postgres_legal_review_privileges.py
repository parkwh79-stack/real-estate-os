from __future__ import annotations

import pytest

from db.postgres_legal_review_privileges import (
    LEGAL_REVIEW_EXTENSION_EXECUTE_FUNCTIONS,
    LEGAL_REVIEW_EXTENSION_SCHEMA,
    LEGAL_REVIEW_EXECUTE_FUNCTIONS,
    LEGAL_REVIEW_READ_PRIVILEGES,
    LEGAL_REVIEW_WRITE_PRIVILEGES,
    LegalReviewPrivilegeError,
    audit_legal_review_role,
    render_legal_review_role_sql,
    validate_identifier,
)


def test_legal_review_role_is_exactly_scoped():
    assert {
        "project_legal_citation_review_versions",
        "project_legal_citation_review_events",
        "project_legal_relation_review_versions",
        "project_legal_relation_review_events",
        "legal_review_decisions",
        "legal_approval_delegations",
        "legal_waivers",
        "counsel_opinions",
    } <= set(LEGAL_REVIEW_WRITE_PRIVILEGES)
    assert {
        "legal_citations",
        "legal_relation_assertions",
        "legal_evidence_snapshots",
        "legal_evidence_snapshot_items",
        "legal_resource_version_lineages",
        "project_legal_review_versions",
        "projects",
        "glossary_terms",
    } <= set(LEGAL_REVIEW_READ_PRIVILEGES)
    assert "legal_citations" not in LEGAL_REVIEW_WRITE_PRIVILEGES
    assert "legal_relation_assertions" not in LEGAL_REVIEW_WRITE_PRIVILEGES
    assert "legal_citation_review_events" not in (
        LEGAL_REVIEW_WRITE_PRIVILEGES
    )
    assert "legal_relation_review_events" not in (
        LEGAL_REVIEW_WRITE_PRIVILEGES
    )
    assert set(LEGAL_REVIEW_WRITE_PRIVILEGES.values()) == {
        ("SELECT", "INSERT")
    }
    assert set(LEGAL_REVIEW_READ_PRIVILEGES.values()) == {("SELECT",)}


def test_legal_review_role_sql_is_append_only_and_fail_closed():
    statements = render_legal_review_role_sql(
        role_name="realytics_legal_review",
        schema_name="realytics",
    )
    sql = "\n".join(statements)
    for marker in (
        "NOLOGIN",
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOINHERIT",
        "NOREPLICATION",
        "NOBYPASSRLS",
    ):
        assert marker in sql
    assert "PASSWORD" not in sql
    assert (
        'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "realytics"'
        in sql
    )
    for table in LEGAL_REVIEW_WRITE_PRIVILEGES:
        assert (
            f'GRANT SELECT, INSERT ON TABLE "realytics"."{table}"'
            in sql
        )
    for table in LEGAL_REVIEW_READ_PRIVILEGES:
        assert (
            f'GRANT SELECT ON TABLE "realytics"."{table}"'
            in sql
        )
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql
    assert LEGAL_REVIEW_EXECUTE_FUNCTIONS == (
        "legal_try_timestamptz(TEXT)",
    )
    assert LEGAL_REVIEW_EXTENSION_SCHEMA == "extensions"
    assert LEGAL_REVIEW_EXTENSION_EXECUTE_FUNCTIONS == (
        "digest(bytea, text)",
    )
    assert (
        'GRANT EXECUTE ON FUNCTION '
        '"realytics".legal_try_timestamptz(TEXT) '
        'TO "realytics_legal_review";'
    ) in sql
    assert 'GRANT USAGE ON SCHEMA "extensions"' in sql
    assert (
        'GRANT EXECUTE ON FUNCTION "extensions".digest(bytea, text) '
        'TO "realytics_legal_review";'
    ) in sql


@pytest.mark.parametrize(
    "value",
    ["", "has-dash", "has space", "1starts_with_digit", "x" * 64],
)
def test_legal_review_role_identifier_fails_closed(value):
    with pytest.raises(LegalReviewPrivilegeError, match="identifier"):
        validate_identifier(value, label="legal review role")


class _AuditCursor:
    def __init__(self, grants, *, memberships=(), login=False):
        self.grants = list(grants)
        self.memberships = list(memberships)
        self.login = login
        self.statement = ""

    def execute(self, statement, _params=None):
        self.statement = " ".join(str(statement).split())

    def fetchone(self):
        if "FROM pg_roles WHERE rolname" in self.statement:
            return (self.login, False, False, False, False, False, False)
        if "has_schema_privilege" in self.statement:
            return (True, False)
        if "has_function_privilege" in self.statement:
            return (True,)
        return None

    def fetchall(self):
        if "FROM pg_auth_members" in self.statement:
            return [(value,) for value in self.memberships]
        if "information_schema.role_table_grants" in self.statement:
            return self.grants
        return []


def test_legal_review_role_audit_detects_extra_grant_and_membership():
    expected = [
        (table, privilege)
        for table, privileges in {
            **LEGAL_REVIEW_READ_PRIVILEGES,
            **LEGAL_REVIEW_WRITE_PRIVILEGES,
        }.items()
        for privilege in privileges
    ]
    cursor = _AuditCursor(
        [*expected, ("legal_provider_dead_letters", "SELECT")],
        memberships=("postgres",),
    )
    missing, forbidden = audit_legal_review_role(
        cursor,
        role_name="realytics_legal_review",
        schema_name="realytics",
    )
    assert missing == []
    assert (
        "realytics.legal_provider_dead_letters.SELECT" in forbidden
    )
    assert (
        "role:realytics_legal_review:member_of:postgres" in forbidden
    )


def test_legal_review_audit_distinguishes_provisioned_role_and_dsn_login():
    expected = [
        (table, privilege)
        for table, privileges in {
            **LEGAL_REVIEW_READ_PRIVILEGES,
            **LEGAL_REVIEW_WRITE_PRIVILEGES,
        }.items()
        for privilege in privileges
    ]
    scope = {"role_name": "realytics_legal_review", "schema_name": "realytics"}
    assert audit_legal_review_role(_AuditCursor(expected), **scope) == ([], [])
    assert audit_legal_review_role(
        _AuditCursor(expected, login=True), require_login=True, **scope
    ) == ([], [])
    assert audit_legal_review_role(
        _AuditCursor(expected), require_login=True, **scope
    ) == (["role:realytics_legal_review:LOGIN"], [])
