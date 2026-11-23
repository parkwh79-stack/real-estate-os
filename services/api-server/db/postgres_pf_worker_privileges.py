"""Private PF execution role contract; never reuse the serving API grants.

This manifest supports durable execution and draft artifact persistence. It
does not authorize identity administration, human evidence approval, or report
publication. Activation still requires a live full-worker rehearsal and the
shared orchestration/proposal payload approval fields to be separately governed.
"""

from __future__ import annotations

from db.postgres_runtime_privileges import (
    MEMBERSHIP_ADMIN_TABLE_PRIVILEGES,
    PROJECT_RUNTIME_SEQUENCE_PRIVILEGES,
    required_table_privileges,
    forbidden_table_privileges,
    validate_database_identifier,
    LEGAL_INTELLIGENCE_RUNTIME_FUNCTIONS,
    LEGAL_INTELLIGENCE_RUNTIME_EXTENSION_FUNCTIONS,
)


PF_WORKER_DATABASE_ROLE_ENV = "REALYTICS_PF_WORKER_DATABASE_ROLE"
DEFAULT_PF_WORKER_DATABASE_ROLE = "realytics_pf_worker"
_ALL_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

# Reads inherit only the business evidence surface; directory relations remain
# wholly inaccessible. Existing approved Parser/legal records are read-only.
PF_WORKER_TABLE_PRIVILEGES = {
    table: (("SELECT",) if "SELECT" in privileges else ())
    for table, privileges in required_table_privileges().items()
    if table not in MEMBERSHIP_ADMIN_TABLE_PRIVILEGES
}
PF_WORKER_TABLE_PRIVILEGES.update({
    table: () for table in (
        *MEMBERSHIP_ADMIN_TABLE_PRIVILEGES,
        "tenant_invitations", "identity_invitation_token_index", "users", "sessions",
        "report_publications", "user_ai_profiles",
    )
})
# Deny every table reserved by the API's policy, even when it has no required
# grant and thus was absent from required_table_privileges().
for _table in forbidden_table_privileges():
    PF_WORKER_TABLE_PRIVILEGES.setdefault(_table, ())

PF_WORKER_TABLE_PRIVILEGES.update({
    "async_jobs": ("SELECT", "INSERT", "UPDATE"),
    "agent_runs": ("SELECT", "INSERT", "UPDATE"),
    "agent_run_steps": ("SELECT", "INSERT", "UPDATE"),
    "brain_usage_events": ("SELECT", "INSERT", "UPDATE"),
    "runtime_outbox_events": ("SELECT", "INSERT", "UPDATE"),
    "orchestrations": ("SELECT", "INSERT", "UPDATE"),
    "market_evidence": ("SELECT", "INSERT", "UPDATE"),
    "risk_registers": ("SELECT", "INSERT", "UPDATE"),
    "evidence_graphs": ("SELECT", "INSERT", "UPDATE"),
    "assumption_proposals": ("SELECT", "INSERT", "UPDATE"),
    "reports": ("SELECT", "INSERT"),
    "project_snapshots": ("SELECT", "INSERT"),
    "user_activity_logs": ("INSERT",),
})
PF_WORKER_FORBIDDEN_TABLE_PRIVILEGES = {
    table: tuple(p for p in _ALL_TABLE_PRIVILEGES if p not in allowed)
    for table, allowed in PF_WORKER_TABLE_PRIVILEGES.items()
}
PF_WORKER_FORBIDDEN_SEQUENCE_PRIVILEGES = {
    sequence: ("SELECT", "USAGE", "UPDATE")
    for sequence in PROJECT_RUNTIME_SEQUENCE_PRIVILEGES
}


def render_pf_worker_role_sql(
    *, role_name: str, schema_name: str = "realytics",
    postgis_schema_name: str = "extensions", database_name: str,
) -> tuple[str, ...]:
    """Render grants only, after a deployment-role safety/ownership inspection.

    No LOGIN, credentials, memberships, or ownership are created here. Exact
    postflight must run as the direct worker login before activation.
    """
    role = validate_database_identifier(role_name, label="PF worker role")
    schema = validate_database_identifier(schema_name, label="database schema")
    postgis = validate_database_identifier(postgis_schema_name, label="PostGIS schema")
    if not database_name:
        raise ValueError("database_name is required")
    quote = lambda value: '"' + value.replace('"', '""') + '"'
    statements = []
    for scoped_schema in dict.fromkeys((schema, postgis)):
        statements.extend((
            f"REVOKE ALL PRIVILEGES ON SCHEMA {quote(scoped_schema)} FROM {quote(role)}",
            f"GRANT USAGE ON SCHEMA {quote(scoped_schema)} TO {quote(role)}",
        ))
    statements.append(
        f"ALTER ROLE {quote(role)} IN DATABASE {quote(database_name)} "
        f"SET search_path TO {quote(schema)}, {quote(postgis)}, pg_temp"
    )
    # Reset only the dedicated role's complete private relation ACLs. Stale
    # grants on tables outside this manifest must not survive reprovisioning.
    # PUBLIC ACLs are shared: inspect and reject unsafe effective access,
    # rather than silently revoking access for unrelated application roles.
    statements.extend((
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {quote(schema)} FROM {quote(role)}",
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {quote(schema)} FROM {quote(role)}",
    ))
    for table, privileges in PF_WORKER_TABLE_PRIVILEGES.items():
        relation = f"{quote(schema)}.{quote(table)}"
        if privileges:
            statements.append(f"GRANT {', '.join(privileges)} ON TABLE {relation} TO {quote(role)}")
    for scoped_schema, signatures in (
        (schema, LEGAL_INTELLIGENCE_RUNTIME_FUNCTIONS),
        (postgis, LEGAL_INTELLIGENCE_RUNTIME_EXTENSION_FUNCTIONS),
    ):
        for signature in signatures:
            statements.append(f"GRANT EXECUTE ON FUNCTION {quote(scoped_schema)}.{signature} TO {quote(role)}")
    return tuple(statements)
