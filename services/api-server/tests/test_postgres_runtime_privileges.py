from __future__ import annotations

import pytest

from db import postgres
from db.postgres_runtime_privileges import (
    DEFAULT_POSTGIS_SCHEMA,
    DEFAULT_RUNTIME_SCHEMA,
    FORBIDDEN_APPEND_ONLY_PRIVILEGES,
    FORBIDDEN_READ_ONLY_PRIVILEGES,
    LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES,
    LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES,
    LEGAL_INTELLIGENCE_RUNTIME_EXTENSION_FUNCTIONS,
    LEGAL_INTELLIGENCE_TENANT_PRIVILEGES,
    LEGAL_PROVIDER_OPERATIONS_PRIVILEGES,
    MEMBERSHIP_ADMIN_COLUMN_PRIVILEGES,
    MEMBERSHIP_ADMIN_FORBIDDEN_COLUMN_PRIVILEGES,
    MEMBERSHIP_ADMIN_TABLE_PRIVILEGES,
    PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
    PARSER_V2_TABLE_PRIVILEGES,
    PROJECT_RUNTIME_FORBIDDEN_PRIVILEGES,
    PROJECT_RUNTIME_SEQUENCE_FORBIDDEN_PRIVILEGES,
    PROJECT_RUNTIME_SEQUENCE_PRIVILEGES,
    PROJECT_RUNTIME_TABLE_PRIVILEGES,
    REVOKE_PUBLIC_DATABASE_TEMPORARY_ENV,
    RuntimePrivilegeProvisioningError,
    audit_runtime_privileges,
    forbidden_column_privileges,
    forbidden_sequence_privileges,
    provision_parser_v2_runtime_role,
    render_parser_v2_runtime_role_sql,
    required_column_privileges,
    required_sequence_privileges,
    required_table_privileges,
    revoke_public_database_temporary_enabled,
    validate_database_identifier,
)


def test_privilege_manifest_covers_every_parser_rls_table_exactly() -> None:
    assert set(PARSER_V2_TABLE_PRIVILEGES) == set(postgres.PARSER_V2_RLS_TABLES)
    assert PARSER_V2_TABLE_PRIVILEGES["parser_validation_finding_events"] == ()
    assert PARSER_V2_MANAGED_SUPPORT_PRIVILEGES["schema_migrations"] == (
        "SELECT",
    )
    assert PARSER_V2_MANAGED_SUPPORT_PRIVILEGES["runtime_outbox_events"] == (
        "SELECT",
        "INSERT",
        "UPDATE",
    )
    for table, privileges in PARSER_V2_TABLE_PRIVILEGES.items():
        assert not set(privileges).intersection(FORBIDDEN_APPEND_ONLY_PRIVILEGES), table


def test_project_runtime_manifest_supports_crud_without_delete_or_setval() -> None:
    assert PROJECT_RUNTIME_TABLE_PRIVILEGES == {
        "projects": ("SELECT", "INSERT", "UPDATE"),
        "user_activity_logs": ("SELECT", "INSERT"),
    }
    assert PROJECT_RUNTIME_FORBIDDEN_PRIVILEGES == {
        "projects": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
        "user_activity_logs": FORBIDDEN_APPEND_ONLY_PRIVILEGES,
    }
    assert PROJECT_RUNTIME_SEQUENCE_PRIVILEGES == {
        "project_id_sequence": ("USAGE",),
    }
    assert PROJECT_RUNTIME_SEQUENCE_FORBIDDEN_PRIVILEGES == {
        "project_id_sequence": ("SELECT", "UPDATE"),
    }
    assert required_sequence_privileges() == (
        PROJECT_RUNTIME_SEQUENCE_PRIVILEGES
    )
    assert forbidden_sequence_privileges() == (
        PROJECT_RUNTIME_SEQUENCE_FORBIDDEN_PRIVILEGES
    )


def test_membership_admin_manifest_is_column_scoped_and_append_only() -> None:
    assert MEMBERSHIP_ADMIN_TABLE_PRIVILEGES == {
        "principals": (),
        "tenant_memberships": (),
        "tenant_identity_access_events": ("INSERT",),
        "tenants": (),
        "onboarding_profiles": (),
        "onboarding_principal_events": (),
        "platform_onboarding_operators": (),
        "tenant_access_requests": (),
        "tenant_access_request_events": (),
    }
    assert MEMBERSHIP_ADMIN_COLUMN_PRIVILEGES["tenant_memberships"] == {
        "SELECT": (
            "tenant_id",
            "issuer",
            "subject",
            "role",
            "status",
            "membership_version",
            "updated_at",
        ),
        "INSERT": (
            "tenant_id",
            "issuer",
            "subject",
            "role",
            "status",
            "membership_version",
            "membership_source",
            "created_at",
            "updated_at",
        ),
        "UPDATE": ("role", "membership_version", "updated_at"),
    }
    assert MEMBERSHIP_ADMIN_COLUMN_PRIVILEGES["tenants"]["SELECT"] == (
        "tenant_id",
        "name",
        "status",
    )
    assert set(MEMBERSHIP_ADMIN_COLUMN_PRIVILEGES[
        "tenant_access_requests"
    ]) == {"SELECT", "UPDATE"}
    assert "request_id" in MEMBERSHIP_ADMIN_FORBIDDEN_COLUMN_PRIVILEGES[
        "tenant_access_requests"
    ]["INSERT"]
    assert set(MEMBERSHIP_ADMIN_COLUMN_PRIVILEGES[
        "tenant_access_request_events"
    ]) == {"INSERT"}
    assert "membership_source" in (
        MEMBERSHIP_ADMIN_FORBIDDEN_COLUMN_PRIVILEGES[
            "tenant_memberships"
        ]["UPDATE"]
    )
    assert required_column_privileges() == {
        table: dict(grants)
        for table, grants in MEMBERSHIP_ADMIN_COLUMN_PRIVILEGES.items()
    }
    assert forbidden_column_privileges() == {
        table: dict(grants)
        for table, grants in (
            MEMBERSHIP_ADMIN_FORBIDDEN_COLUMN_PRIVILEGES.items()
        )
    }


def test_privilege_manifest_covers_legal_corpus_and_tenant_overlay_exactly() -> None:
    assert set(LEGAL_INTELLIGENCE_TENANT_PRIVILEGES) == {
        *postgres.LEGAL_INTELLIGENCE_RLS_TABLES,
        *(
            set(postgres.LEGAL_GOVERNANCE_RLS_TABLES)
            - set(postgres.LEGAL_PROVIDER_REPLAY_RLS_TABLES)
        ),
    }
    assert set(LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES) == {
        "legal_source_providers",
        "legal_sync_runs",
        "legal_sync_run_events",
        "legal_sync_scope_lease_events",
        "legal_sync_checkpoint_events",
        "legal_corpus_scope_completion_attestations",
        "legal_corpus_required_target_contracts",
        "legal_corpus_coverage_registries",
        "legal_corpus_coverage_contracts",
        "legal_source_fetches",
        "legal_resources",
        "legal_resource_identity_crosswalks",
        "legal_resource_versions",
        "legal_resource_version_lineages",
        "legal_provisions",
        "legal_citations",
        "legal_citation_review_events",
        "legal_relation_assertions",
        "legal_relation_review_events",
        "legal_term_definitions",
        "legal_term_provision_links",
        "legal_term_source_links",
        "legal_term_relations",
        "legal_semantic_artifacts",
    }
    assert set(LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES.values()) == {
        ("SELECT",),
        (),
    }
    assert LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES[
        "legal_citation_review_events"
    ] == ()
    assert LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES[
        "legal_relation_review_events"
    ] == ()
    assert set(LEGAL_INTELLIGENCE_TENANT_PRIVILEGES.values()) == {
        ("SELECT", "INSERT"),
        ("SELECT",),
    }
    for table in (
        "project_legal_citation_review_versions",
        "project_legal_citation_review_events",
        "project_legal_relation_review_versions",
        "project_legal_relation_review_events",
    ):
        assert LEGAL_INTELLIGENCE_TENANT_PRIVILEGES[table] == ("SELECT",)
    assert LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES == {
        "glossary_terms": ("SELECT",)
    }
    assert LEGAL_PROVIDER_OPERATIONS_PRIVILEGES == {
        "legal_provider_quarantine": (),
        "legal_provider_dead_letters": (),
        "legal_provider_metrics": (),
        "legal_provider_replay_objects": (),
        "legal_provider_replay_approval_events": (),
        "legal_provider_replay_requests": (),
        "legal_provider_replay_events": (),
    }
    assert "INSERT" in FORBIDDEN_READ_ONLY_PRIVILEGES
    for table, privileges in LEGAL_INTELLIGENCE_TENANT_PRIVILEGES.items():
        assert not set(privileges).intersection(
            FORBIDDEN_APPEND_ONLY_PRIVILEGES
        ), table


@pytest.mark.parametrize(
    "value",
    ["", "has-dash", "has space", "1starts_with_digit", "x" * 64],
)
def test_database_identifier_validation_fails_closed(value: str) -> None:
    with pytest.raises(RuntimePrivilegeProvisioningError, match="identifier"):
        validate_database_identifier(value, label="runtime role")


class _AuditCursor:
    def __init__(
        self,
        grants: set[tuple[str, str]],
        *,
        schema_usage: dict[str, bool] | None = None,
        schema_create: dict[str, bool] | None = None,
        database_temporary: bool = False,
        sequence_grants: set[tuple[str, str]] | None = None,
        column_grants: set[tuple[str, str, str]] | None = None,
    ):
        self.grants = set(grants)
        self.schema_usage = schema_usage or {}
        self.schema_create = schema_create or {}
        self.database_temporary = database_temporary
        self.sequence_grants = set(sequence_grants or ())
        self.column_grants = set(
            column_grants
            if column_grants is not None
            else {
                (table, column, privilege)
                for table, grants in required_column_privileges().items()
                for privilege, columns in grants.items()
                for column in columns
            }
        )
        self.row = None

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        if "has_database_privilege" in normalized:
            self.row = (self.database_temporary,)
        elif normalized.endswith("'USAGE')"):
            self.row = (self.schema_usage.get(str(params[0]), True),)
        elif normalized.endswith("'CREATE')"):
            self.row = (self.schema_create.get(str(params[0]), False),)
        elif "has_sequence_privilege" in normalized:
            privilege, _schema, sequence = params
            self.row = (
                (sequence, privilege) in self.sequence_grants,
            )
        elif "has_column_privilege" in normalized:
            column, privilege, _schema, table = params
            self.row = (
                (table, column, privilege) in self.column_grants,
            )
        elif "WHEN relation_oid IS NULL THEN FALSE" in normalized:
            privilege, _schema, table = params
            self.row = ((table, privilege) in self.grants,)
        else:
            raise AssertionError(normalized)

    def fetchone(self):
        return self.row


def test_privilege_audit_reports_missing_and_forbidden_effective_grants() -> None:
    grants = {
        (table, privilege)
        for table, privileges in required_table_privileges().items()
        for privilege in privileges
    }
    grants.remove(("schema_migrations", "SELECT"))
    grants.add(("parser_runs", "UPDATE"))
    cursor = _AuditCursor(
        grants,
        schema_usage={"realytics": False, "extensions": False},
        schema_create={"realytics": True, "extensions": True},
        sequence_grants={
            (sequence, privilege)
            for sequence, privileges in required_sequence_privileges().items()
            for privilege in privileges
        },
    )

    missing, forbidden = audit_runtime_privileges(
        cursor,
        schema_name="realytics",
    )

    assert missing == [
        "schema:extensions.USAGE",
        "schema:realytics.USAGE",
        "schema_migrations.SELECT",
    ]
    assert forbidden == [
        "parser_runs.UPDATE",
        "schema:extensions.CREATE",
        "schema:realytics.CREATE",
    ]


def test_privilege_audit_reports_membership_column_drift() -> None:
    grants = {
        (table, privilege)
        for table, privileges in required_table_privileges().items()
        for privilege in privileges
    }
    column_grants = {
        (table, column, privilege)
        for table, column_grants in required_column_privileges().items()
        for privilege, columns in column_grants.items()
        for column in columns
    }
    column_grants.remove(("tenant_memberships", "role", "UPDATE"))
    column_grants.add(
        ("tenant_memberships", "membership_source", "UPDATE")
    )

    missing, forbidden = audit_runtime_privileges(
        _AuditCursor(
            grants,
            column_grants=column_grants,
            sequence_grants={
                (sequence, privilege)
                for sequence, privileges
                in required_sequence_privileges().items()
                for privilege in privileges
            },
        ),
        schema_name="realytics",
    )

    assert missing == ["tenant_memberships.role.UPDATE"]
    assert forbidden == ["tenant_memberships.membership_source.UPDATE"]


def test_privilege_audit_rejects_legal_corpus_write_and_overlay_update() -> None:
    grants = {
        (table, privilege)
        for table, privileges in required_table_privileges().items()
        for privilege in privileges
    }
    grants.add(("legal_resource_versions", "INSERT"))
    grants.add(("legal_review_decisions", "UPDATE"))
    grants.add(("glossary_terms", "DELETE"))
    grants.add(("legal_provider_dead_letters", "INSERT"))
    grants.add(("legal_provider_replay_requests", "SELECT"))

    missing, forbidden = audit_runtime_privileges(
        _AuditCursor(
            grants,
            sequence_grants={
                (sequence, privilege)
                for sequence, privileges
                in required_sequence_privileges().items()
                for privilege in privileges
            },
        ),
        schema_name="realytics",
    )

    assert missing == []
    assert forbidden == [
        "glossary_terms.DELETE",
        "legal_provider_dead_letters.INSERT",
        "legal_provider_replay_requests.SELECT",
        "legal_resource_versions.INSERT",
        "legal_review_decisions.UPDATE",
    ]


def test_privilege_audit_can_require_database_temporary_revocation() -> None:
    grants = {
        (table, privilege)
        for table, privileges in required_table_privileges().items()
        for privilege in privileges
    }

    missing, forbidden = audit_runtime_privileges(
        _AuditCursor(
            grants,
            database_temporary=True,
            sequence_grants={
                (sequence, privilege)
                for sequence, privileges
                in required_sequence_privileges().items()
                for privilege in privileges
            },
        ),
        schema_name="realytics",
        require_no_database_temporary=True,
    )

    assert missing == []
    assert forbidden == ["database.TEMPORARY"]


def test_privilege_audit_reports_missing_or_broad_project_sequence_grants() -> None:
    grants = {
        (table, privilege)
        for table, privileges in required_table_privileges().items()
        for privilege in privileges
    }
    missing, forbidden = audit_runtime_privileges(
        _AuditCursor(
            grants,
            sequence_grants={
                ("project_id_sequence", "SELECT"),
                ("project_id_sequence", "UPDATE"),
            },
        ),
        schema_name="realytics",
    )

    assert missing == ["project_id_sequence.USAGE"]
    assert forbidden == [
        "project_id_sequence.SELECT",
        "project_id_sequence.UPDATE",
    ]


class _ProvisionCursor:
    def __init__(
        self,
        *,
        privileged_role: bool = False,
        replication_role: bool = False,
        owned=(),
        memberships=(),
        current_database: str = "postgres",
    ):
        self.privileged_role = privileged_role
        self.replication_role = replication_role
        self.owned = list(owned)
        self.memberships = list(memberships)
        self.rows = []
        self.statements: list[tuple[str, object]] = []
        self.current_schema = "realytics"
        self.current_database = current_database

    def execute(self, query, params=None):
        if hasattr(query, "as_string"):
            rendered = query.as_string()
        else:
            rendered = str(query)
        normalized = " ".join(rendered.split())
        self.statements.append((normalized, params))
        if normalized == "SELECT current_schema()":
            self.rows = [(self.current_schema,)]
        elif normalized == "SELECT current_database()":
            self.rows = [(self.current_database,)]
        elif normalized.startswith("SELECT rolsuper"):
            self.rows = [
                (
                    self.privileged_role,
                    False,
                    False,
                    False,
                    False,
                    self.replication_role,
                )
            ]
        elif "FROM pg_auth_members AS membership" in normalized:
            self.rows = [(item,) for item in self.memberships]
        elif "owner_role.oid = c.relowner" in normalized:
            self.rows = [(item,) for item in self.owned]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _ProvisionConnection:
    def __init__(self, cursor: _ProvisionCursor):
        self.bound_cursor = cursor

    def cursor(self):
        return self.bound_cursor


def test_provisioner_revokes_parser_tables_and_grants_only_manifest() -> None:
    cursor = _ProvisionCursor()
    provision_parser_v2_runtime_role(
        _ProvisionConnection(cursor),
        role_name="realytics_runtime",
        schema_name="realytics",
        migration_lock_key=postgres.MIGRATION_LOCK_KEY,
    )

    sql = "\n".join(statement for statement, _params in cursor.statements)
    assert DEFAULT_RUNTIME_SCHEMA == "public"
    assert DEFAULT_POSTGIS_SCHEMA == "extensions"
    assert "REVOKE CREATE ON SCHEMA \"realytics\" FROM PUBLIC" in sql
    assert "REVOKE CREATE ON SCHEMA \"extensions\" FROM PUBLIC" in sql
    assert (
        'GRANT USAGE ON SCHEMA "realytics" TO "realytics_runtime"'
        in sql
    )
    assert (
        'GRANT USAGE ON SCHEMA "extensions" TO "realytics_runtime"'
        in sql
    )
    assert LEGAL_INTELLIGENCE_RUNTIME_EXTENSION_FUNCTIONS == (
        "digest(bytea, text)",
    )
    assert (
        'GRANT EXECUTE ON FUNCTION "extensions".digest(bytea, text) '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'ALTER ROLE "realytics_runtime" IN DATABASE "postgres" '
        'SET search_path TO "realytics", "extensions", "pg_temp"'
    ) in sql
    assert "REVOKE TEMPORARY ON DATABASE" not in sql
    assert (
        'GRANT SELECT, INSERT ON TABLE "realytics"."parser_runs" '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT SELECT, INSERT, UPDATE ON TABLE '
        '"realytics"."runtime_outbox_events" '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT SELECT, INSERT, UPDATE ON TABLE '
        '"realytics"."projects" TO "realytics_runtime"'
    ) in sql
    assert (
        'REVOKE ALL PRIVILEGES ON TABLE '
        '"realytics"."projects" FROM "realytics_runtime"'
    ) in sql
    assert (
        'GRANT SELECT, INSERT ON TABLE "realytics"."user_activity_logs" '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT SELECT ("issuer", "subject", "principal_type", "status", '
        '"email", "display_name") ON TABLE "realytics"."principals" '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT UPDATE ("role", "membership_version", "updated_at") '
        'ON TABLE "realytics"."tenant_memberships" '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT INSERT ON TABLE "realytics".'
        '"tenant_identity_access_events" TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT UPDATE ON TABLE "realytics"."tenant_memberships"'
        not in sql
    )
    assert (
        'GRANT USAGE ON SEQUENCE '
        '"realytics"."project_id_sequence" TO "realytics_runtime"'
    ) in sql
    assert (
        'REVOKE ALL PRIVILEGES ON SEQUENCE '
        '"realytics"."project_id_sequence" FROM "realytics_runtime"'
    ) in sql
    assert (
        'GRANT UPDATE ON SEQUENCE "realytics"."project_id_sequence"'
        not in sql
    )
    assert 'GRANT SELECT ON TABLE "realytics"."schema_migrations"' in sql
    assert (
        'GRANT SELECT ON TABLE "realytics"."legal_resource_versions" '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT SELECT ON TABLE "realytics"."legal_review_decisions" '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT SELECT ON TABLE '
        '"realytics"."project_legal_citation_review_versions" '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT SELECT, INSERT ON TABLE '
        '"realytics"."project_legal_citation_review_versions"'
        not in sql
    )
    assert (
        'GRANT SELECT ON TABLE "realytics"."glossary_terms" '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT INSERT ON TABLE "realytics"."legal_resource_versions"'
        not in sql
    )
    assert (
        'GRANT UPDATE ON TABLE "realytics"."legal_review_decisions"'
        not in sql
    )
    assert (
        'GRANT INSERT ON TABLE "realytics"."legal_provider_dead_letters"'
        not in sql
    )
    assert (
        'GRANT SELECT ON TABLE "realytics"."legal_provider_replay_requests"'
        not in sql
    )
    assert (
        'REVOKE ALL PRIVILEGES ON TABLE '
        '"realytics"."legal_provider_replay_requests" '
        'FROM "realytics_runtime"'
    ) in sql
    assert 'GRANT UPDATE ON TABLE "realytics"."parser_runs"' not in sql
    assert 'GRANT DELETE' not in sql
    assert 'TO "anon"' not in sql
    assert 'TO "authenticated"' not in sql
    assert 'TO "service_role"' not in sql
    validation_event_grants = [
        statement
        for statement, _params in cursor.statements
        if "parser_validation_finding_events" in statement
        and statement.startswith("GRANT")
    ]
    assert validation_event_grants == []


def test_rendered_manifest_matches_private_schema_contract() -> None:
    statements = render_parser_v2_runtime_role_sql(
        role_name="realytics_runtime",
        schema_name="realytics",
        postgis_schema_name="extensions",
        database_name="postgres",
    )
    sql = "\n".join(statements)

    assert (
        'ALTER ROLE "realytics_runtime" IN DATABASE "postgres" '
        'SET search_path TO "realytics", "extensions", "pg_temp"'
    ) in sql
    assert 'GRANT USAGE ON SCHEMA "extensions" TO "realytics_runtime"' in sql
    assert 'GRANT CREATE ON SCHEMA "extensions"' not in sql
    assert (
        'GRANT EXECUTE ON FUNCTION '
        '"realytics".legal_try_timestamptz(TEXT) '
        'TO "realytics_runtime"'
    ) in sql
    assert 'TO "anon"' not in sql
    assert 'TO "authenticated"' not in sql
    assert 'TO "service_role"' not in sql


def test_rendered_manifest_can_opt_into_shared_database_temp_revocation() -> None:
    statements = render_parser_v2_runtime_role_sql(
        role_name="realytics_runtime",
        schema_name="realytics",
        postgis_schema_name="extensions",
        database_name="postgres",
        revoke_public_database_temporary=True,
    )

    assert (
        'REVOKE TEMPORARY ON DATABASE "postgres" FROM PUBLIC'
        in statements
    )
    assert (
        'REVOKE TEMPORARY ON DATABASE "postgres" '
        'FROM "realytics_runtime"'
        in statements
    )


def test_database_temp_revocation_requires_explicit_environment_opt_in(
    monkeypatch,
) -> None:
    monkeypatch.delenv(REVOKE_PUBLIC_DATABASE_TEMPORARY_ENV, raising=False)
    assert revoke_public_database_temporary_enabled() is False

    monkeypatch.setenv(REVOKE_PUBLIC_DATABASE_TEMPORARY_ENV, "true")
    assert revoke_public_database_temporary_enabled() is True


def test_rendered_manifest_requires_database_name() -> None:
    with pytest.raises(RuntimePrivilegeProvisioningError, match="database name"):
        render_parser_v2_runtime_role_sql(
            role_name="realytics_runtime",
            schema_name="realytics",
            postgis_schema_name="extensions",
            database_name="",
        )


@pytest.mark.parametrize(
    ("cursor", "message"),
    [
        (_ProvisionCursor(privileged_role=True), "NOSUPERUSER"),
        (_ProvisionCursor(replication_role=True), "NOREPLICATION"),
        (_ProvisionCursor(owned=("parser_runs",)), "must not own"),
        (_ProvisionCursor(memberships=("dangerous_parent",)), "member of other roles"),
    ],
)
def test_provisioner_rejects_privileged_or_owning_runtime_role(
    cursor: _ProvisionCursor,
    message: str,
) -> None:
    with pytest.raises(RuntimePrivilegeProvisioningError, match=message):
        provision_parser_v2_runtime_role(
            _ProvisionConnection(cursor),
            role_name="realytics_runtime",
            schema_name="realytics",
            migration_lock_key=postgres.MIGRATION_LOCK_KEY,
        )


def test_provisioner_rejects_dsn_search_path_for_another_schema() -> None:
    cursor = _ProvisionCursor()
    cursor.current_schema = "other_schema"

    with pytest.raises(RuntimePrivilegeProvisioningError, match="search_path"):
        provision_parser_v2_runtime_role(
            _ProvisionConnection(cursor),
            role_name="realytics_runtime",
            schema_name="realytics",
            migration_lock_key=postgres.MIGRATION_LOCK_KEY,
        )


def test_provisioner_deduplicates_public_or_shared_extension_schema() -> None:
    cursor = _ProvisionCursor()
    cursor.current_schema = "public"

    provision_parser_v2_runtime_role(
        _ProvisionConnection(cursor),
        role_name="realytics_runtime",
        schema_name="public",
        postgis_schema_name="public",
        migration_lock_key=postgres.MIGRATION_LOCK_KEY,
    )

    sql = "\n".join(statement for statement, _params in cursor.statements)
    assert sql.count('REVOKE CREATE ON SCHEMA "public" FROM PUBLIC') == 1
    assert (
        'SET search_path TO "public", "pg_temp"'
    ) in sql


def test_provisioner_quotes_server_supplied_database_name() -> None:
    cursor = _ProvisionCursor(current_database='project "primary"')

    provision_parser_v2_runtime_role(
        _ProvisionConnection(cursor),
        role_name="realytics_runtime",
        schema_name="realytics",
        migration_lock_key=postgres.MIGRATION_LOCK_KEY,
    )

    sql = "\n".join(statement for statement, _params in cursor.statements)
    assert (
        'ALTER ROLE "realytics_runtime" IN DATABASE "project ""primary""" '
        'SET search_path TO "realytics", "extensions", "pg_temp"'
    ) in sql


def test_provisioner_rejects_invalid_postgis_schema_identifier() -> None:
    with pytest.raises(RuntimePrivilegeProvisioningError, match="PostGIS schema"):
        provision_parser_v2_runtime_role(
            _ProvisionConnection(_ProvisionCursor()),
            role_name="realytics_runtime",
            schema_name="realytics",
            postgis_schema_name="has-dash",
            migration_lock_key=postgres.MIGRATION_LOCK_KEY,
        )
