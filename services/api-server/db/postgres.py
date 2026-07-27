from __future__ import annotations

"""PostgreSQL / PostGIS connection, config, and schema bootstrap.

This is the foundation layer for moving persistence off SQLite. It builds the
DSN from environment, lazily connects via psycopg (clear error if the driver is
not installed), and holds the PostGIS-enabled schema DDL. Routing every store to
Postgres + the live data migration is the deployment step; this module is
unit-tested for config/DSN/DDL without a live database.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import quote_plus

# Authoritative schema lives in a versioned .sql migration so a DBA and the app
# share one source of truth (faithful mirror of storage._ensure_sqlite_schema).
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
INITIAL_SCHEMA_FILE = MIGRATIONS_DIR / "0001_initial_schema.sql"
MIGRATION_LOCK_KEY = 7_731_590_012
PARSER_V2_RLS_TABLES = (
    "parser_document_versions",
    "parser_project_profiles",
    "parser_runs",
    "parser_run_events",
    "parser_candidate_set_seals",
    "parsed_fact_candidates",
    "parser_candidate_inputs",
    "parser_validation_findings",
    "parser_validation_finding_events",
    "parser_review_decisions",
    "parser_source_conflicts",
    "parser_source_conflict_events",
    "parser_conflict_candidates",
    "parser_review_decision_conflicts",
    "parser_canonical_selections",
    "parser_approved_snapshots",
    "parser_snapshot_selections",
    "parser_snapshot_source_runs",
    "parser_validity_events",
)
LEGAL_INTELLIGENCE_RLS_TABLES = (
    "legal_query_runs",
    "legal_query_run_events",
    "project_legal_resource_links",
    "legal_findings",
    "legal_evidence_snapshots",
    "legal_evidence_snapshot_items",
    "legal_review_decisions",
)
LEGAL_GOVERNANCE_RLS_TABLES = (
    "legal_approval_delegations",
    "legal_required_claim_sets",
    "legal_required_claims",
    "project_legal_matters",
    "legal_waivers",
    "counsel_opinions",
    "legal_analysis_versions",
    "project_legal_annotations",
    "tenant_legal_term_aliases",
    "tenant_legal_term_alias_review_events",
    "project_legal_review_versions",
    "project_permit_review_versions",
    "project_legal_citation_review_versions",
    "project_legal_citation_review_events",
    "project_legal_relation_review_versions",
    "project_legal_relation_review_events",
    "legal_provider_replay_objects",
    "legal_provider_replay_approval_events",
    "legal_provider_replay_requests",
    "legal_provider_replay_events",
)
LEGAL_PROVIDER_REPLAY_RLS_TABLES = (
    "legal_provider_replay_objects",
    "legal_provider_replay_approval_events",
    "legal_provider_replay_requests",
    "legal_provider_replay_events",
)
REPORT_PUBLICATION_RLS_TABLES = (
    "reports",
    "report_publications",
)
LEGAL_INTELLIGENCE_MIGRATION = "0008_legal_intelligence_core.sql"
LEGAL_GOVERNANCE_MIGRATION = "0009_legal_governance_and_tenant_overlay.sql"
REPORT_PUBLICATION_MIGRATION = (
    "20260727002612_normalized_report_publication.sql"
)


class MigrationChecksumMismatch(RuntimeError):
    """Raised when an already-applied migration was edited in place."""


class ParserV2RuntimeReadinessError(RuntimeError):
    """Normalized Parser V2 cannot safely serve traffic with this DB role/schema."""


def postgres_configured() -> bool:
    return bool(os.environ.get("REALYTICS_DATABASE_URL") or os.environ.get("REALYTICS_PG_HOST"))


def build_dsn() -> Optional[str]:
    """Build a libpq DSN/URL from env, or None if not configured.

    Precedence: REALYTICS_DATABASE_URL (full URL) wins; otherwise assemble from
    REALYTICS_PG_HOST/PORT/DB/USER/PASSWORD (sensible defaults).
    """
    url = os.environ.get("REALYTICS_DATABASE_URL")
    if url:
        return url
    host = os.environ.get("REALYTICS_PG_HOST")
    if not host:
        return None
    port = os.environ.get("REALYTICS_PG_PORT", "5432")
    db = os.environ.get("REALYTICS_PG_DB", "realytics")
    user = os.environ.get("REALYTICS_PG_USER", "realytics")
    password = os.environ.get("REALYTICS_PG_PASSWORD", "")
    auth = f"{quote_plus(user)}:{quote_plus(password)}@" if password else f"{quote_plus(user)}@"
    return f"postgresql://{auth}{host}:{port}/{db}"


def safe_dsn(dsn: Optional[str]) -> Optional[str]:
    """Redact the password for logging/status."""
    if not dsn or "@" not in dsn:
        return dsn
    scheme_sep = "://"
    if scheme_sep not in dsn:
        return dsn
    scheme, rest = dsn.split(scheme_sep, 1)
    creds, host = rest.split("@", 1)
    if ":" in creds:
        user = creds.split(":", 1)[0]
        creds = f"{user}:***"
    return f"{scheme}{scheme_sep}{creds}@{host}"


def connect(dsn: Optional[str] = None):
    """Open a psycopg connection. Lazy-imports psycopg with a clear error."""
    dsn = dsn or build_dsn()
    if not dsn:
        raise RuntimeError("PostgreSQL is not configured (set REALYTICS_DATABASE_URL or REALYTICS_PG_HOST).")
    try:
        import psycopg  # noqa: WPS433  (lazy: only when Postgres backend is used)
    except ImportError as exc:  # pragma: no cover - depends on deploy env
        raise RuntimeError(
            "PostgreSQL selected but psycopg is not installed. "
            "Add psycopg to requirements and `pip install -r requirements.txt`."
        ) from exc
    return psycopg.connect(dsn)


# PostGIS extension + a JSONB-per-store schema mirroring the SQLite stores, plus
# a spatial table for Map Intelligence (land parcels with a geometry column).
POSTGIS_BOOTSTRAP_SQL = "CREATE EXTENSION IF NOT EXISTS postgis;"

JSONB_STORE_TABLES: List[str] = [
    "projects",
    "orchestrations",
    "reports",
    "market_evidence",
    "land_evidence",
    "risk_registers",
    "evidence_graphs",
    "chief_pod_artifacts",
    "pod_ai_reviews",
    "agent_runs",
    "agent_run_steps",
    "brain_usage_events",
    "runtime_outbox_events",
    "brain_provider_health",
    "brain_routing_policies",
    "brain_routing_events",
    "tool_calls",
    "learning_candidates",
    "learning_evaluations",
    "learning_promotions",
    "coding_pod_jobs",
    "prompt_artifacts",
    "assumption_overrides",
    "restructuring_cures",
    "glossary_terms",
    "glossary_term_candidates",
    "project_snapshots",
    "async_jobs",
    "assumption_proposals",
    "project_versions",
]


def _split_sql_statements(sql: str) -> List[str]:
    """Split PostgreSQL SQL without cutting quoted bodies or comments.

    Migrations contain COMMENT strings and may contain PL/pgSQL dollar-quoted
    bodies. A plain ``str.split(';')`` corrupts either form, so this scanner
    treats only a top-level semicolon as a statement terminator.
    """

    statements: List[str] = []
    current: List[str] = []
    index = 0
    state = "normal"
    dollar_tag = ""
    block_depth = 0

    while index < len(sql):
        char = sql[index]
        following = sql[index : index + 2]

        if state == "line_comment":
            if char in "\r\n":
                current.append("\n")
                state = "normal"
            index += 1
            continue

        if state == "block_comment":
            if following == "/*":
                block_depth += 1
                index += 2
                continue
            if following == "*/":
                block_depth -= 1
                index += 2
                if block_depth == 0:
                    current.append(" ")
                    state = "normal"
                continue
            index += 1
            continue

        if state == "single_quote":
            current.append(char)
            if char == "'":
                if index + 1 < len(sql) and sql[index + 1] == "'":
                    current.append("'")
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "double_quote":
            current.append(char)
            if char == '"':
                if index + 1 < len(sql) and sql[index + 1] == '"':
                    current.append('"')
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "dollar_quote":
            if sql.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                state = "normal"
                continue
            current.append(char)
            index += 1
            continue

        if following == "--":
            state = "line_comment"
            index += 2
            continue
        if following == "/*":
            state = "block_comment"
            block_depth = 1
            index += 2
            continue
        if char == "'":
            current.append(char)
            state = "single_quote"
            index += 1
            continue
        if char == '"':
            current.append(char)
            state = "double_quote"
            index += 1
            continue
        if char == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", sql[index:])
            if match is not None:
                dollar_tag = match.group(0)
                current.append(dollar_tag)
                index += len(dollar_tag)
                state = "dollar_quote"
                continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(f"{statement};")
            current = []
            index += 1
            continue
        current.append(char)
        index += 1

    trailing = "".join(current).strip()
    if trailing:
        statements.append(f"{trailing};")
    return statements


def schema_ddl() -> List[str]:
    """All DDL statements to bootstrap a fresh PostgreSQL/PostGIS database.

    Loads every authoritative versioned migration in filename order so fresh and
    existing databases receive the same typed schema as the SQLite stores. The
    migrations are idempotent and do not rely on a generic JSONB-only fallback.
    """
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if migration_files:
        statements: List[str] = []
        for migration_file in migration_files:
            statements.extend(_split_sql_statements(migration_file.read_text(encoding="utf-8")))
        return statements
    # Fallback (should not happen in a packaged deploy): minimal spatial bootstrap.
    return [
        POSTGIS_BOOTSTRAP_SQL,
        "CREATE TABLE IF NOT EXISTS land_parcels (pnu TEXT PRIMARY KEY, project_id TEXT, "
        "official_land_price NUMERIC, geom geometry(MultiPolygon, 4326), payload JSONB);",
        "CREATE INDEX IF NOT EXISTS idx_land_parcels_geom ON land_parcels USING GIST(geom);",
    ]


def _migration_files() -> List[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _migration_checksum(path: Path) -> str:
    # Git and deployment packaging may materialize CRLF or LF.  Migration
    # identity is based on canonical SQL text, not the host line-ending style.
    canonical_text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _read_migration_ledger(cursor) -> Dict[str, str] | None:
    # Resolve the ledger only in the active application schema.  An
    # unqualified ``to_regclass`` can fall through to a later search_path entry
    # (commonly ``public``), causing a fresh tenant/application schema to appear
    # fully migrated when only the public schema has a ledger.
    cursor.execute(
        """
        SELECT c.oid
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
          AND c.relname = 'schema_migrations'
          AND c.relkind IN ('r', 'p')
        """
    )
    relation = cursor.fetchone()
    if not relation or relation[0] is None:
        return None
    cursor.execute("SELECT version, checksum FROM schema_migrations")
    return {
        str(version): str(checksum)
        for version, checksum in cursor.fetchall()
    }


def _assert_recorded_checksums(
    expected: Mapping[str, str], applied: Mapping[str, str]
) -> None:
    changed = sorted(
        version
        for version, checksum in expected.items()
        if version in applied and applied[version] != checksum
    )
    if changed:
        raise MigrationChecksumMismatch(
            "Applied PostgreSQL migration checksum differs from the repository copy: "
            + ", ".join(changed)
            + ". Create a new migration instead of editing an applied migration."
        )


def ensure_schema(conn) -> None:
    """Apply each immutable migration once, guarded by checksum and a DB lock.

    Runtime repositories still call this compatibility seam before doing work,
    so the ledger is intentionally cheap on the steady-state path.  All pending
    migrations are applied in one transaction and the transaction-scoped
    advisory lock prevents two application instances from racing at startup.
    """
    try:
        with conn.cursor() as cur:
            migration_files = _migration_files()
            expected = {
                path.name: _migration_checksum(path)
                for path in migration_files
            }
            applied = _read_migration_ledger(cur)
            if applied is not None:
                _assert_recorded_checksums(expected, applied)
                if set(expected).issubset(applied):
                    # Steady-state runtime path: read-only verification only.
                    # The serving role therefore does not need DDL privileges.
                    conn.commit()
                    return

            cur.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            applied = _read_migration_ledger(cur) or {}
            _assert_recorded_checksums(expected, applied)

            if not migration_files:
                for statement in schema_ddl():
                    cur.execute(statement)
            else:
                for migration_file in migration_files:
                    version = migration_file.name
                    checksum = _migration_checksum(migration_file)
                    recorded_checksum = applied.get(version)
                    if recorded_checksum is not None:
                        continue
                    for statement in _split_sql_statements(
                        migration_file.read_text(encoding="utf-8")
                    ):
                        cur.execute(statement)
                    cur.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                        (version, checksum),
                    )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def assert_parser_v2_runtime_ready() -> None:
    """Fail fast unless migrations, RLS, and the application role are safe.

    This probe is deliberately read-only and must run with the runtime DSN, not
    the deployment/migration DSN.  FORCE RLS is defense in depth; the serving
    role must still be neither privileged nor the owner of Parser V2 or Legal
    Intelligence tenant tables.  The public function name is retained because
    deployment and startup callers already use it as the normalized-runtime
    readiness seam.
    """
    conn = connect()
    try:
        expected = {
            path.name: _migration_checksum(path)
            for path in _migration_files()
        }
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT version, checksum FROM schema_migrations")
                applied = {
                    str(version): str(checksum)
                    for version, checksum in cur.fetchall()
                }
            except Exception as exc:
                raise ParserV2RuntimeReadinessError(
                    "PostgreSQL migrations are not initialized; run the deployment "
                    "migration command with the migration role before startup."
                ) from exc

            missing = sorted(set(expected).difference(applied))
            changed = sorted(
                version
                for version, checksum in expected.items()
                if version in applied and applied[version] != checksum
            )
            if missing or changed:
                details = []
                if missing:
                    details.append("missing=" + ",".join(missing))
                if changed:
                    details.append("checksum_mismatch=" + ",".join(changed))
                raise ParserV2RuntimeReadinessError(
                    "PostgreSQL migration state is not current (" + "; ".join(details) + ")"
                )

            cur.execute(
                "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, "
                "rolinherit, rolbypassrls, rolreplication "
                "FROM pg_roles WHERE rolname = current_user"
            )
            role = cur.fetchone()
            if role is None or any(bool(value) for value in role[1:]):
                raise ParserV2RuntimeReadinessError(
                    "Parser V2 runtime role must not be superuser, CREATEDB, "
                    "CREATEROLE, INHERIT, BYPASSRLS, or REPLICATION"
                )

            cur.execute(
                """
                SELECT parent.rolname
                FROM pg_auth_members AS membership
                JOIN pg_roles AS member ON member.oid = membership.member
                JOIN pg_roles AS parent ON parent.oid = membership.roleid
                WHERE member.rolname = current_user
                ORDER BY parent.rolname
                """
            )
            inherited_roles = [str(row[0]) for row in cur.fetchall()]
            if inherited_roles:
                raise ParserV2RuntimeReadinessError(
                    "Parser V2 runtime role must not be a member of other "
                    "database roles: " + ", ".join(inherited_roles)
                )

            required_rls_tables = list(PARSER_V2_RLS_TABLES)
            if LEGAL_INTELLIGENCE_MIGRATION in expected:
                required_rls_tables.extend(LEGAL_INTELLIGENCE_RLS_TABLES)
            if LEGAL_GOVERNANCE_MIGRATION in expected:
                required_rls_tables.extend(LEGAL_GOVERNANCE_RLS_TABLES)
            if REPORT_PUBLICATION_MIGRATION in expected:
                required_rls_tables.extend(REPORT_PUBLICATION_RLS_TABLES)

            cur.execute(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       pg_get_userbyid(c.relowner) = current_user AS owned_by_runtime
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
                """,
                (required_rls_tables,),
            )
            states = {
                str(name): (bool(enabled), bool(forced), bool(owned))
                for name, enabled, forced, owned in cur.fetchall()
            }
            unsafe = sorted(
                table
                for table in required_rls_tables
                if table not in states
                or not states[table][0]
                or not states[table][1]
                or states[table][2]
            )
            if unsafe:
                raise ParserV2RuntimeReadinessError(
                    "Parser V2 / Legal Intelligence tables are missing, not "
                    "FORCE-RLS protected, or owned by the runtime role: "
                    + ", ".join(unsafe)
                )

            cur.execute("SELECT current_schema()")
            schema_row = cur.fetchone()
            schema_name = str(schema_row[0] or "") if schema_row else ""
            from db.postgres_runtime_privileges import (  # noqa: WPS433
                DEFAULT_POSTGIS_SCHEMA,
                POSTGIS_SCHEMA_ENV,
                RuntimePrivilegeProvisioningError,
                audit_runtime_privileges,
                revoke_public_database_temporary_enabled,
            )

            try:
                missing_privileges, forbidden_privileges = audit_runtime_privileges(
                    cur,
                    schema_name=schema_name,
                    postgis_schema_name=os.environ.get(
                        POSTGIS_SCHEMA_ENV,
                        DEFAULT_POSTGIS_SCHEMA,
                    ),
                    require_no_database_temporary=(
                        revoke_public_database_temporary_enabled()
                    ),
                )
            except RuntimePrivilegeProvisioningError as exc:
                raise ParserV2RuntimeReadinessError(str(exc)) from exc
            if missing_privileges or forbidden_privileges:
                details = []
                if missing_privileges:
                    details.append("missing=" + ",".join(missing_privileges))
                if forbidden_privileges:
                    details.append("forbidden=" + ",".join(forbidden_privileges))
                raise ParserV2RuntimeReadinessError(
                    "Parser V2 runtime privilege manifest is unsafe ("
                    + "; ".join(details)
                    + ")"
                )
    finally:
        conn.close()


_TYPED_JSONB_TABLES: Dict[str, Dict[str, Any]] = {
    "projects": {
        "key": ("project_id",),
        "columns": ("project_id", "tenant_id", "created_by", "updated_at"),
        "order": "updated_at DESC, project_id DESC",
    },
    "async_jobs": {
        "key": ("id",),
        "columns": (
            "id", "project_id", "tenant_id", "kind", "status", "attempt_count",
            "max_attempts", "lease_owner", "lease_expires_at", "created_at", "updated_at",
        ),
        "order": "created_at ASC, id ASC",
    },
    "assumption_proposals": {
        "key": ("proposal_id",),
        "columns": (
            "proposal_id", "project_id", "run_id", "field_path", "source_agent", "status",
            "created_at", "reviewed_at",
        ),
        "order": "created_at DESC, proposal_id DESC",
    },
    "project_versions": {
        "key": ("version_id",),
        "columns": (
            "version_id", "project_id", "tenant_id", "version", "base_version",
            "created_by", "created_at", "state_hash",
        ),
        "order": "version DESC, created_at DESC",
    },
    "agent_runs": {
        "key": ("run_id",),
        "columns": (
            "run_id", "project_id", "tenant_id", "user_id", "request_id",
            "correlation_id", "trace_id", "agent_id", "agent_version", "task_type",
            "idempotency_key", "input_hash", "status", "attempt_count", "max_attempts",
            "cancellation_requested", "state_version", "lease_owner", "lease_expires_at",
            "heartbeat_at", "deadline_at", "consumed_cost_usd", "consumed_llm_calls",
            "consumed_tool_calls", "retry_of_run_id", "selected_brain_profile_id",
            "created_at", "updated_at", "started_at", "completed_at",
        ),
        "order": "updated_at DESC, run_id DESC",
    },
    "agent_run_steps": {
        "key": ("step_id",),
        "columns": (
            "step_id", "run_id", "project_id", "sequence_no", "stage", "step_type",
            "status", "attempt_no", "error_code", "started_at", "completed_at",
        ),
        "order": "sequence_no ASC, step_id ASC",
    },
    "brain_usage_events": {
        "key": ("event_id",),
        "columns": (
            "event_id", "run_id", "step_id", "tenant_id", "project_id", "agent_id",
            "agent_version", "brain_profile_id", "provider", "model", "status",
            "input_tokens", "output_tokens", "estimated_cost_usd", "latency_ms", "occurred_at",
        ),
        "order": "occurred_at ASC, event_id ASC",
    },
    "runtime_outbox_events": {
        "key": ("event_id",),
        "columns": (
            "event_id", "tenant_id", "project_id", "aggregate_type", "aggregate_id",
            "event_type", "idempotency_key", "status", "attempt_count", "max_attempts",
            "available_at", "lease_owner", "lease_expires_at", "created_at", "updated_at",
            "published_at",
        ),
        "order": "created_at ASC, event_id ASC",
    },
    "brain_provider_health": {
        "key": ("health_id",),
        "columns": (
            "health_id", "tenant_id", "brain_profile_id", "provider", "model", "status",
            "consecutive_failures", "checked_at", "next_check_at",
        ),
        "order": "checked_at DESC, health_id DESC",
    },
    "brain_routing_policies": {
        "key": ("policy_id",),
        "columns": ("policy_id", "tenant_id", "user_id", "version", "active", "created_at"),
        "order": "version DESC, created_at DESC",
    },
    "brain_routing_events": {
        "key": ("routing_event_id",),
        "columns": (
            "routing_event_id", "run_id", "request_id", "tenant_id", "user_id", "project_id",
            "task_type", "policy_id", "policy_version", "selected_profile_id",
            "selected_provider", "selected_model", "blocked", "occurred_at",
        ),
        "order": "occurred_at DESC, routing_event_id DESC",
    },
    "tool_calls": {
        "key": ("tool_call_id",),
        "columns": (
            "tool_call_id", "run_id", "step_id", "tenant_id", "user_id", "project_id",
            "tool_id", "tool_version", "status", "side_effect_level", "idempotency_key",
            "state_version", "requested_at", "completed_at",
        ),
        "order": "requested_at DESC, tool_call_id DESC",
    },
    "learning_candidates": {
        "key": ("candidate_id",),
        "columns": (
            "candidate_id", "tenant_id", "project_id", "candidate_type", "target_key", "status",
            "created_by", "created_at", "updated_at",
        ),
        "order": "updated_at DESC, candidate_id DESC",
    },
    "learning_evaluations": {
        "key": ("evaluation_id",),
        "columns": ("evaluation_id", "candidate_id", "tenant_id", "passed", "evaluated_at"),
        "order": "evaluated_at DESC, evaluation_id DESC",
    },
    "learning_promotions": {
        "key": ("decision_id",),
        "columns": (
            "decision_id", "candidate_id", "tenant_id", "action", "environment",
            "approved_by", "approved_at",
        ),
        "order": "approved_at DESC, decision_id DESC",
    },
    "coding_pod_jobs": {
        "key": ("job_id",),
        "columns": (
            "job_id", "tenant_id", "requested_by", "workspace_ref", "branch_name", "status",
            "idempotency_key", "lease_owner", "lease_expires_at", "heartbeat_at", "created_at",
            "attempt_count", "max_attempts", "state_version", "updated_at",
        ),
        "order": "created_at ASC, job_id ASC",
    },
    "chief_pod_artifacts": {
        "key": ("project_id", "artifact_key"),
        "columns": ("project_id", "artifact_key", "pod_key", "artifact_type", "status", "created_at", "updated_at"),
        "order": "created_at DESC, artifact_key DESC",
    },
    "pod_ai_reviews": {
        "key": ("project_id", "review_id"),
        "columns": ("project_id", "review_id", "pod_key", "source_artifact_key", "agent_id", "review_status", "ai_brain_used_event_id", "created_at", "updated_at"),
        "order": "updated_at DESC, review_id DESC",
    },
    "agent_handoffs": {
        "key": ("project_id", "handoff_id"),
        "columns": ("project_id", "handoff_id", "run_id", "from_agent", "to_agent", "handoff_type", "status", "created_at", "updated_at"),
        "order": "created_at DESC, handoff_id DESC",
    },
    "recalculation_events": {
        "key": ("project_id", "event_id"),
        "columns": ("project_id", "event_id", "run_id", "source_pod", "target_pod", "trigger_name", "event_type", "status", "created_at", "updated_at"),
        "order": "created_at DESC, event_id DESC",
    },
    "calculation_runs": {
        "key": ("project_id", "calculation_run_id"),
        "columns": ("project_id", "calculation_run_id", "engine_key", "engine_version", "status", "created_at", "updated_at"),
        "order": "created_at DESC, calculation_run_id DESC",
    },
    "deal_state_records": {
        "key": ("project_id", "record_id"),
        "columns": ("project_id", "record_id", "record_type", "key", "calculation_run_id", "source_type", "review_status", "updated_at"),
        "order": "updated_at DESC, record_id DESC",
    },
}


def claim_async_job_record(*, worker_id: str, now, lease_expires_at) -> Dict[str, Any] | None:
    """Claim one queued/expired asynchronous job with SKIP LOCKED semantics."""
    conn = connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, payload_json
                FROM async_jobs
                WHERE attempt_count < max_attempts AND (
                    status = 'queued'
                    OR (status = 'running' AND lease_expires_at < %s)
                )
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (now,),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None
            job_id, raw_payload = row
            payload = dict(raw_payload)
            payload.update(
                {
                    "status": "running",
                    "startedAt": payload.get("startedAt") or now.isoformat(),
                    "leaseOwner": worker_id,
                    "leaseExpiresAt": lease_expires_at.isoformat(),
                    "attemptCount": int(payload.get("attemptCount") or 0) + 1,
                    "updatedAt": now.isoformat(),
                }
            )
            cur.execute(
                """
                UPDATE async_jobs
                SET status = 'running', attempt_count = %s, lease_owner = %s,
                    lease_expires_at = %s, updated_at = %s, payload_json = %s::jsonb
                WHERE id = %s
                """,
                (
                    payload["attemptCount"],
                    worker_id,
                    lease_expires_at,
                    now,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    job_id,
                ),
            )
        conn.commit()
        return payload
    finally:
        conn.close()


def insert_typed_jsonb_record(
    table: str,
    values: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    conn=None,
) -> bool:
    """Insert an immutable/idempotent runtime row and report whether it won."""
    spec = _TYPED_JSONB_TABLES.get(table)
    if spec is None:
        raise ValueError(f"Unsupported typed PostgreSQL table: {table}")
    columns = [column for column in spec["columns"] if column in values]
    missing_keys = [column for column in spec["key"] if column not in values]
    if missing_keys:
        raise ValueError(f"Missing PostgreSQL key columns for {table}: {', '.join(missing_keys)}")
    insert_columns = [*columns, "payload_json"]
    placeholders = ["%s" for _ in columns] + ["%s::jsonb"]
    sql = (
        f"INSERT INTO {table}({', '.join(insert_columns)}) "
        f"VALUES({', '.join(placeholders)}) ON CONFLICT DO NOTHING"
    )
    params = [values[column] for column in columns]
    params.append(json.dumps(payload, ensure_ascii=False, default=str))
    owns = conn is None
    if owns:
        conn = connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            created = cur.rowcount == 1
        conn.commit()
        return created
    finally:
        if owns:
            conn.close()


def update_agent_run_optimistic(
    values: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    expected_state_version: int,
    conn=None,
) -> bool:
    """Update one Agent run only when its persisted state version still matches."""
    run_id = values.get("run_id")
    if not run_id:
        raise ValueError("run_id is required")
    spec = _TYPED_JSONB_TABLES["agent_runs"]
    columns = [
        column for column in spec["columns"]
        if column in values and column not in {"run_id"}
    ]
    assignments = [f"{column} = %s" for column in columns]
    assignments.append("payload_json = %s::jsonb")
    sql = (
        f"UPDATE agent_runs SET {', '.join(assignments)} "
        "WHERE run_id = %s AND state_version = %s"
    )
    params = [values[column] for column in columns]
    params.extend([
        json.dumps(payload, ensure_ascii=False, default=str),
        run_id,
        expected_state_version,
    ])
    owns = conn is None
    if owns:
        conn = connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            updated = cur.rowcount == 1
        conn.commit()
        return updated
    finally:
        if owns:
            conn.close()


def update_agent_run_with_outbox(
    values: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    expected_state_version: int,
    outbox_values: Dict[str, Any],
    outbox_payload: Dict[str, Any],
) -> bool:
    """Atomically persist one AgentRun transition and its durable outbox event."""
    conn = connect()
    try:
        ensure_schema(conn)
        run_spec = _TYPED_JSONB_TABLES["agent_runs"]
        columns = [
            column for column in run_spec["columns"]
            if column in values and column != "run_id"
        ]
        assignments = [f"{column} = %s" for column in columns]
        assignments.append("payload_json = %s::jsonb")
        run_params = [values[column] for column in columns]
        run_params.extend([
            json.dumps(payload, ensure_ascii=False, default=str),
            values["run_id"],
            expected_state_version,
        ])
        outbox_spec = _TYPED_JSONB_TABLES["runtime_outbox_events"]
        outbox_columns = [column for column in outbox_spec["columns"] if column in outbox_values]
        outbox_sql = (
            f"INSERT INTO runtime_outbox_events({', '.join(outbox_columns)}, payload_json) "
            f"VALUES({', '.join('%s' for _ in outbox_columns)}, %s::jsonb) "
            "ON CONFLICT DO NOTHING"
        )
        outbox_params = [outbox_values[column] for column in outbox_columns]
        outbox_params.append(json.dumps(outbox_payload, ensure_ascii=False, default=str))
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE agent_runs SET {', '.join(assignments)} "
                "WHERE run_id = %s AND state_version = %s",
                run_params,
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False
            cur.execute(outbox_sql, outbox_params)
        conn.commit()
        return True
    finally:
        conn.close()


def _typed_upsert_command(
    table: str,
    values: Dict[str, Any],
    payload: Dict[str, Any],
) -> tuple[str, list[Any]]:
    spec = _TYPED_JSONB_TABLES.get(table)
    if spec is None:
        raise ValueError(f"Unsupported typed PostgreSQL table: {table}")
    columns = [column for column in spec["columns"] if column in values]
    missing_keys = [column for column in spec["key"] if column not in values]
    if missing_keys:
        raise ValueError(f"Missing PostgreSQL key columns for {table}: {', '.join(missing_keys)}")
    insert_columns = [*columns, "payload_json"]
    placeholders = ["%s" for _ in columns] + ["%s::jsonb"]
    update_columns = [column for column in columns if column not in spec["key"]]
    update_columns.append("payload_json")
    update_clause = ", ".join(f"{column}=EXCLUDED.{column}" for column in update_columns)
    sql = (
        f"INSERT INTO {table}({', '.join(insert_columns)}) "
        f"VALUES({', '.join(placeholders)}) "
        f"ON CONFLICT ({', '.join(spec['key'])}) DO UPDATE SET {update_clause}"
    )
    params = [values[column] for column in columns]
    params.append(json.dumps(payload, ensure_ascii=False, default=str))
    return sql, params


def upsert_typed_jsonb_record(table: str, values: Dict[str, Any], payload: Dict[str, Any], *, conn=None) -> None:
    """Write a critical audit record to its first-class PostgreSQL table."""
    sql, params = _typed_upsert_command(table, values, payload)
    owns = conn is None
    if owns:
        conn = connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        if owns:
            conn.close()


def upsert_typed_jsonb_records(
    table: str,
    records: List[tuple[Dict[str, Any], Dict[str, Any]]],
    *,
    conn=None,
) -> None:
    """Write a homogeneous typed-record batch using one schema check and commit."""
    if not records:
        return
    commands = [_typed_upsert_command(table, values, payload) for values, payload in records]
    owns = conn is None
    if owns:
        conn = connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            for sql, params in commands:
                cur.execute(sql, params)
        conn.commit()
    finally:
        if owns:
            conn.close()


def upsert_typed_jsonb_record_with_outbox(
    table: str,
    values: Dict[str, Any],
    payload: Dict[str, Any],
    outbox_values: Dict[str, Any],
    outbox_payload: Dict[str, Any],
) -> None:
    """Commit a typed aggregate and its delivery event in one transaction."""
    aggregate_sql, aggregate_params = _typed_upsert_command(table, values, payload)
    outbox_sql, outbox_params = _typed_upsert_command(
        "runtime_outbox_events", outbox_values, outbox_payload,
    )
    conn = connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(aggregate_sql, aggregate_params)
            cur.execute(outbox_sql, outbox_params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_typed_jsonb_record(table: str, where: Dict[str, Any], *, conn=None) -> Optional[Dict[str, Any]]:
    rows = list_typed_jsonb_records(table, where=where, limit=1, conn=conn)
    return rows[0] if rows else None


def list_typed_jsonb_records(
    table: str,
    *,
    where: Dict[str, Any] | None = None,
    limit: int = 100,
    conn=None,
) -> List[Dict[str, Any]]:
    spec = _TYPED_JSONB_TABLES.get(table)
    if spec is None:
        raise ValueError(f"Unsupported typed PostgreSQL table: {table}")
    where = where or {}
    allowed = set(spec["columns"])
    if any(column not in allowed for column in where):
        raise ValueError(f"Unsupported PostgreSQL filter for {table}")
    clauses = [f"{column} = %s" for column in where]
    sql = f"SELECT payload_json FROM {table}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" ORDER BY {spec['order']} LIMIT %s"
    params = [*where.values(), max(1, min(int(limit), 1000))]
    owns = conn is None
    if owns:
        conn = connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        records: List[Dict[str, Any]] = []
        for row in rows:
            payload = row[0]
            records.append(json.loads(payload) if isinstance(payload, (str, bytes, bytearray)) else payload)
        return records
    finally:
        if owns:
            conn.close()


def claim_runtime_outbox_records(
    *,
    worker_id: str,
    now,
    lease_expires_at,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Claim due outbox rows with PostgreSQL row locking and skip-locked semantics."""
    conn = connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_id, payload_json
                FROM runtime_outbox_events
                WHERE (
                    (status IN ('pending', 'retrying') AND available_at <= %s)
                    OR (status = 'processing' AND lease_expires_at < %s)
                )
                ORDER BY available_at, created_at
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (now, now, max(1, min(int(limit), 100))),
            )
            rows = cur.fetchall()
            claimed: List[Dict[str, Any]] = []
            for event_id, raw_payload in rows:
                payload = dict(raw_payload)
                payload.update({
                    "status": "processing",
                    "leaseOwner": worker_id,
                    "leaseExpiresAt": lease_expires_at.isoformat(),
                    "updatedAt": now.isoformat(),
                    "attemptCount": int(payload.get("attemptCount") or 0) + 1,
                })
                cur.execute(
                    """
                    UPDATE runtime_outbox_events
                    SET status = 'processing', attempt_count = %s, lease_owner = %s,
                        lease_expires_at = %s, updated_at = %s, payload_json = %s::jsonb
                    WHERE event_id = %s
                    """,
                    (
                        payload["attemptCount"], worker_id, lease_expires_at, now,
                        json.dumps(payload, ensure_ascii=False, default=str), event_id,
                    ),
                )
                claimed.append(payload)
        conn.commit()
        return claimed
    finally:
        conn.close()


def claim_coding_pod_job_record(*, worker_id: str, now, lease_expires_at) -> Dict[str, Any] | None:
    """Claim one Coding Pod job with row locking and expired-worker recovery."""
    conn = connect()
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id, payload_json
                FROM coding_pod_jobs
                WHERE attempt_count < max_attempts AND (
                    status = 'queued'
                    OR (status IN ('leased', 'running') AND lease_expires_at < %s)
                )
                ORDER BY created_at, job_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (now,),
            )
            row = cur.fetchone()
            if row is None:
                conn.commit()
                return None
            job_id, raw_payload = row
            payload = dict(raw_payload)
            payload.update({
                "status": "leased",
                "leaseOwner": worker_id,
                "leaseExpiresAt": lease_expires_at.isoformat(),
                "heartbeatAt": now.isoformat(),
                "attemptCount": int(payload.get("attemptCount") or 0) + 1,
                "maxAttempts": int(payload.get("maxAttempts") or 3),
                "stateVersion": int(payload.get("stateVersion") or 1) + 1,
                "updatedAt": now.isoformat(),
            })
            cur.execute(
                """
                UPDATE coding_pod_jobs
                SET status = 'leased', lease_owner = %s, lease_expires_at = %s,
                    heartbeat_at = %s, attempt_count = %s, max_attempts = %s,
                    state_version = %s, updated_at = %s, payload_json = %s::jsonb
                WHERE job_id = %s
                """,
                (
                    worker_id, lease_expires_at, now, payload["attemptCount"],
                    payload["maxAttempts"], payload["stateVersion"], now,
                    json.dumps(payload, ensure_ascii=False, default=str), job_id,
                ),
            )
        conn.commit()
        return payload
    finally:
        conn.close()


# --- State document store --------------------------------------------------
# Dual-backend v1: the whole application state is persisted as a single JSONB
# document row in `app_state`, mirroring the JSON-file backend but on Postgres
# (durable, network-shared, backup-friendly). This is the safe env-switch step;
# native per-first-class-table persistence (using the 0001 schema) is a later
# deployment optimization. Connection is injectable so the mapping is unit-
# testable without a live database or the psycopg driver.

APP_STATE_TABLE = "app_state"
APP_STATE_ROW_ID = "singleton"

APP_STATE_DDL = (
    f"CREATE TABLE IF NOT EXISTS {APP_STATE_TABLE} ("
    "id TEXT PRIMARY KEY, document JSONB NOT NULL, "
    "updated_at TIMESTAMPTZ DEFAULT now());"
)


def _ensure_app_state_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(APP_STATE_DDL)
    conn.commit()


def save_state_document(state: Dict[str, Any], *, conn=None) -> None:
    """Persist the entire state dict as one JSONB row (upsert). Opens/owns a
    connection when one is not injected."""
    import json as _json

    owns = conn is None
    if owns:
        conn = connect()
    try:
        _ensure_app_state_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {APP_STATE_TABLE}(id, document, updated_at) "
                f"VALUES (%s, %s::jsonb, now()) "
                f"ON CONFLICT (id) DO UPDATE SET document = EXCLUDED.document, updated_at = now()",
                (APP_STATE_ROW_ID, _json.dumps(state, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        if owns:
            conn.close()


def load_state_document(*, conn=None) -> Optional[Dict[str, Any]]:
    """Read the state document, or None when it has not been written yet.

    Accepts JSONB (driver returns a dict) or a JSON string, so it works across
    psycopg configurations and the unit-test fakes."""
    import json as _json

    owns = conn is None
    if owns:
        conn = connect()
    try:
        _ensure_app_state_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT document FROM {APP_STATE_TABLE} WHERE id = %s",
                (APP_STATE_ROW_ID,),
            )
            row = cur.fetchone()
        if not row or row[0] is None:
            return None
        document = row[0]
        return _json.loads(document) if isinstance(document, (str, bytes, bytearray)) else document
    finally:
        if owns:
            conn.close()


def status() -> Dict[str, Any]:
    dsn = build_dsn()
    driver_available = True
    try:
        import psycopg  # noqa: F401
    except ImportError:
        driver_available = False
    return {
        "configured": postgres_configured(),
        "dsn": safe_dsn(dsn),
        "driverInstalled": driver_available,
        "postgisBootstrap": POSTGIS_BOOTSTRAP_SQL,
        "tableCount": len(JSONB_STORE_TABLES) + 1,
        "stateStore": {"mode": "state_document_v1", "table": APP_STATE_TABLE},
    }
