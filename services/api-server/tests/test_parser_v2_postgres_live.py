"""Optional live PostgreSQL contract tests for normalized Parser V2.

Set ``REALYTICS_TEST_DATABASE_URL`` to an isolated, disposable database.  The
suite creates a uniquely named schema plus a temporary unprivileged runtime role
and applies the production privilege provisioner.  It skips when the supplied
deployment role cannot create/SET ROLE.  It never falls back to the normal
application DSN.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from db import postgres
from db.postgres_runtime_privileges import (
    audit_runtime_privileges,
    provision_parser_v2_runtime_role,
)
from db.repositories.parser_v2_repository import (
    APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
    ParserV2PostgresRepository,
)


_POSTGRES_CONNECT = postgres.connect


TEST_DSN = os.environ.get("REALYTICS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DSN,
    reason="REALYTICS_TEST_DATABASE_URL is not configured",
)

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_GENERATED_TIMESTAMPS = frozenset(
    {
        "createdAt",
        "updatedAt",
        "requestedAt",
        "ingestedAt",
        "detectedAt",
        "decidedAt",
        "selectedAt",
        "approvedAt",
        "resolvedAt",
        "startedAt",
        "completedAt",
    }
)

TENANT_A = "parser-live-tenant-a"
TENANT_B = "parser-live-tenant-b"
PROJECT_A = "parser-live-project-a"
PROJECT_B = "parser-live-project-b"
PROFILE_A = "parser-live-profile-a"
PROFILE_B = "parser-live-profile-b"
PROFILE_VERSION = "1.0.0"
PROFILE_A_HASH = "1" * 64
PROFILE_A_PLAN_HASH = "2" * 64
PROFILE_B_HASH = "3" * 64
PROFILE_B_PLAN_HASH = "4" * 64


def _validated_identifier(prefix: str) -> str:
    value = f"{prefix}_{uuid4().hex}"
    if not _SAFE_IDENTIFIER.fullmatch(value):
        raise AssertionError(f"unsafe PostgreSQL test identifier: {value!r}")
    return value


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _with_record_hash(record: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(record)
    payload = {
        key: value
        for key, value in result.items()
        if key not in _GENERATED_TIMESTAMPS.union(
            {"audit", "immutablePayloadHash"}
        )
    }
    result["immutablePayloadHash"] = _stable_hash(payload)
    return result


@dataclass(frozen=True)
class _LiveDatabase:
    dsn: str
    schema_name: str
    runtime_role: str | None
    first_migration_ledger: dict[str, str]
    second_migration_ledger: dict[str, str]

    def connect(self, *, runtime: bool = False):
        from psycopg import sql

        # Keep the fixture connection factory independent from the readiness
        # test's monkeypatch of ``postgres.connect``.  Otherwise the patched
        # factory recursively calls itself instead of opening the admin DSN.
        conn = _POSTGRES_CONNECT(self.dsn)
        with conn.cursor() as cursor:
            cursor.execute(
                sql.SQL("SET search_path TO {}, public").format(
                    sql.Identifier(self.schema_name)
                )
            )
            if runtime and self.runtime_role:
                cursor.execute(
                    sql.SQL("SET ROLE {}").format(
                        sql.Identifier(self.runtime_role)
                    )
                )
        conn.commit()
        return conn

    def connect_admin(self):
        return self.connect(runtime=False)

    def connect_runtime(self):
        return self.connect(runtime=True)


def _migration_ledger(conn: Any) -> dict[str, str]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT version, checksum FROM schema_migrations")
        return {str(version): str(checksum) for version, checksum in cursor.fetchall()}


def _seed_tenants_projects_and_profiles(conn: Any) -> None:
    now = "2026-07-15T00:00:00+00:00"
    with conn.cursor() as cursor:
        for tenant_id in (TENANT_A, TENANT_B):
            cursor.execute(
                """
                INSERT INTO tenants(
                    tenant_id, name, status, created_at, updated_at, metadata_json
                ) VALUES (%s, %s, 'active', %s, %s, '{}'::jsonb)
                """,
                (tenant_id, tenant_id, now, now),
            )
        for tenant_id, project_id in (
            (TENANT_A, PROJECT_A),
            (TENANT_B, PROJECT_B),
        ):
            cursor.execute(
                """
                INSERT INTO projects(
                    project_id, payload_json, updated_at, tenant_id, created_by
                ) VALUES (%s, %s::jsonb, %s, %s, 'live-test')
                """,
                (
                    project_id,
                    json.dumps({"projectId": project_id, "tenantId": tenant_id}),
                    now,
                    tenant_id,
                ),
            )
    conn.commit()

    for tenant_id, project_id, profile_id, profile_hash, plan_hash in (
        (
            TENANT_A,
            PROJECT_A,
            PROFILE_A,
            PROFILE_A_HASH,
            PROFILE_A_PLAN_HASH,
        ),
        (
            TENANT_B,
            PROJECT_B,
            PROFILE_B,
            PROFILE_B_HASH,
            PROFILE_B_PLAN_HASH,
        ),
    ):
        payload = {
            "tenantId": tenant_id,
            "projectId": project_id,
            "profileId": profile_id,
            "profileVersion": PROFILE_VERSION,
        }
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('realytics.tenant_id', %s, true)",
                (tenant_id,),
            )
            cursor.execute(
                """
                INSERT INTO parser_project_profiles(
                    tenant_id, project_id, profile_id, profile_version,
                    configuration_status, profile_hash, plan_hash,
                    configured_by, configured_at, payload_json
                ) VALUES (
                    %s, %s, %s, %s, 'active', %s, %s,
                    'live-test', %s, %s::jsonb
                )
                """,
                (
                    tenant_id,
                    project_id,
                    profile_id,
                    PROFILE_VERSION,
                    profile_hash,
                    plan_hash,
                    now,
                    json.dumps(payload),
                ),
            )
        conn.commit()


@pytest.fixture(scope="module")
def live_database() -> _LiveDatabase:
    from psycopg import sql

    assert TEST_DSN is not None
    schema_name = _validated_identifier("parser_v2_live")
    candidate_role = _validated_identifier("parser_v2_runtime")
    runtime_role: str | None = None
    conn = postgres.connect(TEST_DSN)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name))
            )
            cursor.execute(
                sql.SQL("SET search_path TO {}, public").format(
                    sql.Identifier(schema_name)
                )
            )
        conn.commit()

        postgres.ensure_schema(conn)
        first_ledger = _migration_ledger(conn)
        postgres.ensure_schema(conn)
        second_ledger = _migration_ledger(conn)

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user, rolsuper, rolcreaterole, rolbypassrls
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
            admin_user, is_superuser, can_create_role, bypasses_rls = cursor.fetchone()

        if not (bool(is_superuser) or bool(can_create_role)):
            pytest.skip(
                "live Parser V2 role tests require an isolated database role "
                "that can create and SET ROLE to a temporary runtime principal"
            )
        with conn.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOINHERIT NOBYPASSRLS"
                ).format(sql.Identifier(candidate_role))
            )
            cursor.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(candidate_role),
                    sql.Identifier(str(admin_user)),
                )
            )
        provision_parser_v2_runtime_role(
            conn,
            role_name=candidate_role,
            schema_name=schema_name,
            migration_lock_key=postgres.MIGRATION_LOCK_KEY,
        )
        conn.commit()
        runtime_role = candidate_role

        database = _LiveDatabase(
            dsn=TEST_DSN,
            schema_name=schema_name,
            runtime_role=runtime_role,
            first_migration_ledger=first_ledger,
            second_migration_ledger=second_ledger,
        )
        _seed_tenants_projects_and_profiles(conn)
        yield database
    finally:
        conn.rollback()
        try:
            with conn.cursor() as cursor:
                cursor.execute("RESET ROLE")
                cursor.execute("SET search_path TO public")
            conn.commit()
            with conn.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )
                if runtime_role:
                    cursor.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(
                            sql.Identifier(runtime_role)
                        )
                    )
            conn.commit()
        finally:
            conn.close()


def test_live_migrations_apply_once_and_are_idempotent(
    live_database: _LiveDatabase,
) -> None:
    expected = {
        path.name: postgres._migration_checksum(path)
        for path in sorted(Path(postgres.MIGRATIONS_DIR).glob("*.sql"))
    }
    assert live_database.first_migration_ledger == expected
    assert live_database.second_migration_ledger == expected

    conn = live_database.connect_admin()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = ANY(%s)
                ORDER BY c.relname
                """,
                (live_database.schema_name, list(postgres.PARSER_V2_RLS_TABLES)),
            )
            rows = cursor.fetchall()
        assert [row[0] for row in rows] == sorted(postgres.PARSER_V2_RLS_TABLES)
        assert all(bool(row[1]) and bool(row[2]) for row in rows)
    finally:
        conn.close()


def test_live_rls_fails_closed_and_hides_other_tenants(
    live_database: _LiveDatabase,
) -> None:
    import psycopg

    conn = live_database.connect_runtime()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT NULLIF(current_setting('realytics.tenant_id', true), '')"
            )
            assert cursor.fetchone()[0] is None
            cursor.execute("SELECT count(*) FROM parser_project_profiles")
            assert cursor.fetchone()[0] == 0
        conn.rollback()

        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('realytics.tenant_id', %s, true)",
                (TENANT_A,),
            )
            cursor.execute(
                "SELECT tenant_id, project_id, profile_id "
                "FROM parser_project_profiles ORDER BY tenant_id"
            )
            assert cursor.fetchall() == [(TENANT_A, PROJECT_A, PROFILE_A)]

            with pytest.raises(psycopg.Error) as exc_info:
                cursor.execute(
                    """
                    INSERT INTO parser_project_profiles(
                        tenant_id, project_id, profile_id, profile_version,
                        configuration_status, profile_hash, plan_hash,
                        configured_by, configured_at, payload_json
                    ) VALUES (
                        %s, %s, 'cross-tenant-write', '1.0.0', 'active',
                        %s, %s, 'live-test', NOW(), '{}'::jsonb
                    )
                    """,
                    (TENANT_B, PROJECT_B, "5" * 64, "6" * 64),
                )
            assert exc_info.value.sqlstate == "42501"
        conn.rollback()
    finally:
        conn.close()


def test_live_runtime_role_lacks_append_only_update_privilege(
    live_database: _LiveDatabase,
) -> None:
    import psycopg

    conn = live_database.connect_runtime()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('realytics.tenant_id', %s, true)",
                (TENANT_A,),
            )
            with pytest.raises(psycopg.Error) as exc_info:
                cursor.execute(
                    """
                    UPDATE parser_project_profiles
                    SET configuration_status = 'retired'
                    WHERE tenant_id = %s AND project_id = %s
                      AND profile_id = %s AND profile_version = %s
                    """,
                    (TENANT_A, PROJECT_A, PROFILE_A, PROFILE_VERSION),
                )
            assert exc_info.value.sqlstate == "42501"
        conn.rollback()
    finally:
        conn.close()


def test_live_runtime_readiness_and_exact_privilege_manifest(
    live_database: _LiveDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(postgres, "connect", live_database.connect_runtime)

    postgres.assert_parser_v2_runtime_ready()

    conn = live_database.connect_runtime()
    try:
        with conn.cursor() as cursor:
            missing, forbidden = audit_runtime_privileges(
                cursor,
                schema_name=live_database.schema_name,
            )
        assert missing == []
        assert forbidden == []
    finally:
        conn.rollback()
        conn.close()


def test_live_project_advisory_lock_serializes_two_connections(
    live_database: _LiveDatabase,
) -> None:
    import psycopg

    first = live_database.connect_runtime()
    second = live_database.connect_runtime()
    repository = ParserV2PostgresRepository()
    try:
        with first.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            first_pid = cursor.fetchone()[0]
        with second.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            second_pid = cursor.fetchone()[0]
        assert first_pid != second_pid

        repository.bind(first, TENANT_A, PROJECT_A)
        with second.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', '250ms', true)")
        with pytest.raises(psycopg.Error) as exc_info:
            repository.bind(second, TENANT_A, PROJECT_A)
        assert exc_info.value.sqlstate == "55P03"

        second.rollback()
        first.rollback()
        repository.bind(second, TENANT_A, PROJECT_A)
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()


def _seed_snapshot_authority(live_database: _LiveDatabase) -> None:
    now = "2026-07-15T01:00:00+00:00"
    document_version_id = "parser-live-document-version"
    parsing_run_id = "parser-live-run"
    candidate_id = "parser-live-candidate"
    review_id = "parser-live-review"
    selection_id = "parser-live-selection"
    selection_payload = {
        "selectionId": selection_id,
        "fieldKey": "project.type",
        "entityId": None,
        "candidateId": candidate_id,
        "reviewDecisionId": review_id,
        "approvalStatus": "approved",
        "normalizedValue": "development",
        "selectionVersion": 1,
    }
    conn = live_database.connect_admin()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('realytics.tenant_id', %s, true)",
                (TENANT_A,),
            )
            cursor.execute(
                """
                INSERT INTO parser_document_versions(
                    document_version_id, tenant_id, project_id, document_id,
                    archive_id, object_version, sha256, file_name, media_type,
                    size_bytes, version_number, source_authority, schema_version,
                    supersedes_document_version_id, ingested_by, ingested_at,
                    payload_json
                ) VALUES (
                    %s, %s, %s, 'parser-live-document', 'archive-live',
                    'object-version-1', %s, 'pilot.xlsx',
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    1, 1, 'uploaded', 'document-v1', NULL, 'live-test', %s,
                    %s::jsonb
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    document_version_id,
                    TENANT_A,
                    PROJECT_A,
                    "7" * 64,
                    now,
                    json.dumps({"documentVersionId": document_version_id}),
                ),
            )
            cursor.execute(
                """
                INSERT INTO parser_runs(
                    parsing_run_id, tenant_id, project_id, document_version_id,
                    execution_status, approval_status, parser_version,
                    ontology_version, prompt_version, model_version, profile_id,
                    profile_version, plan_hash, requested_modules, idempotency_key,
                    input_hash, parent_parsing_run_id, attempt_number, requested_at,
                    payload_json
                ) VALUES (
                    %s, %s, %s, %s, 'pending', 'not_requested', 'parser-live',
                    'ontology-live', 'prompt-live', 'model-live', %s, %s, %s,
                    ARRAY['structure'], 'parser-live-idempotency', %s, NULL, 1,
                    %s, %s::jsonb
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    parsing_run_id,
                    TENANT_A,
                    PROJECT_A,
                    document_version_id,
                    PROFILE_A,
                    PROFILE_VERSION,
                    PROFILE_A_PLAN_HASH,
                    "8" * 64,
                    now,
                    json.dumps({"parsingRunId": parsing_run_id}),
                ),
            )
            cursor.execute(
                """
                INSERT INTO parsed_fact_candidates(
                    candidate_id, tenant_id, project_id, parsing_run_id,
                    document_version_id, field_key, entity_id, raw_value,
                    normalized_value, unit, origin_type, parser_version,
                    ontology_version, prompt_version, model_version,
                    field_registry_version, candidate_hash, created_at, payload_json
                ) VALUES (
                    %s, %s, %s, %s, %s, 'project.type', NULL,
                    '"development"'::jsonb, '"development"'::jsonb, NULL,
                    'extracted', 'parser-live', 'ontology-live', 'prompt-live',
                    'model-live', 'registry-live', %s, %s, %s::jsonb
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    candidate_id,
                    TENANT_A,
                    PROJECT_A,
                    parsing_run_id,
                    document_version_id,
                    "9" * 64,
                    now,
                    json.dumps({"candidateId": candidate_id}),
                ),
            )
            cursor.execute(
                """
                INSERT INTO parser_review_decisions(
                    review_decision_id, tenant_id, project_id, candidate_id,
                    decision_sequence, action, resulting_review_status,
                    resulting_approval_status, reviewer_id, decided_at, reason,
                    decision_hash, payload_json
                ) VALUES (
                    %s, %s, %s, %s, 1, 'approve', 'approved', 'approved',
                    'live-reviewer', %s, 'live approval', %s, %s::jsonb
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    review_id,
                    TENANT_A,
                    PROJECT_A,
                    candidate_id,
                    now,
                    "a" * 64,
                    json.dumps({"reviewDecisionId": review_id}),
                ),
            )
            cursor.execute(
                """
                INSERT INTO parser_canonical_selections(
                    selection_id, tenant_id, project_id, field_key, entity_id,
                    candidate_id, review_decision_id, approval_status,
                    conflict_status, normalized_value, unit,
                    field_registry_version, selection_version, selection_hash,
                    selected_by, selected_at, effective_date, as_of_date,
                    supersedes_selection_id, payload_json
                ) VALUES (
                    %s, %s, %s, 'project.type', NULL, %s, %s, 'approved',
                    'none', '"development"'::jsonb, NULL, 'registry-live', 1,
                    %s, 'live-reviewer', %s, NULL, NULL, NULL, %s::jsonb
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    selection_id,
                    TENANT_A,
                    PROJECT_A,
                    candidate_id,
                    review_id,
                    "b" * 64,
                    now,
                    json.dumps(selection_payload),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _snapshot_record(
    snapshot_id: str = "parser-live-snapshot",
    purpose: str = "cash_flow",
) -> dict[str, Any]:
    return _with_record_hash(
        {
            "schemaVersion": "realytics-parser-v2-record-v1",
            "recordType": APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
            "recordId": f"{APPROVED_INPUT_SNAPSHOT_RECORD_TYPE}:{snapshot_id}",
            "eventKind": "created",
            "tenantId": TENANT_A,
            "projectId": PROJECT_A,
            "actorId": "live-approver",
            "snapshotId": snapshot_id,
            "purpose": purpose,
            "snapshotVersion": 1,
            "approvalStatus": "approved",
            "inputSchemaVersion": "inputs-live",
            "ontologyVersion": "ontology-live",
            "fieldRegistryVersion": "registry-live",
            "selectionIds": ["parser-live-selection"],
            "selections": [
                {
                    "selectionId": "parser-live-selection",
                    "fieldKey": "project.type",
                    "entityId": None,
                    "candidateId": "parser-live-candidate",
                    "reviewDecisionId": "parser-live-review",
                    "approvalStatus": "approved",
                    "normalizedValue": "development",
                    "selectionVersion": 1,
                }
            ],
            "sourceParsingRunIds": ["parser-live-run"],
            "approvedBy": "live-approver",
            "approvedAt": "2026-07-15T01:05:00+00:00",
            "createdAt": "2026-07-15T01:05:00+00:00",
        }
    )


def test_live_snapshot_replay_after_outbox_publish_is_idempotent(
    live_database: _LiveDatabase,
) -> None:
    _seed_snapshot_authority(live_database)
    repository = ParserV2PostgresRepository(live_database.connect_runtime)
    snapshot = _snapshot_record(
        "parser-live-replay-snapshot",
        purpose="outbox_replay",
    )

    first = repository.append_record(TENANT_A, PROJECT_A, snapshot)
    admin = live_database.connect_admin()
    try:
        lifecycle = {
            "status": "published",
            "attemptCount": 1,
            "availableAt": "2026-07-15T01:05:00+00:00",
            "leaseOwner": None,
            "leaseExpiresAt": None,
            "lastError": None,
            "updatedAt": "2026-07-15T01:06:00+00:00",
            "publishedAt": "2026-07-15T01:06:00+00:00",
        }
        with admin.cursor() as cursor:
            cursor.execute(
                """
                UPDATE runtime_outbox_events
                SET status = 'published', attempt_count = 1,
                    available_at = %s, lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = %s, published_at = %s,
                    payload_json = payload_json || %s::jsonb
                WHERE tenant_id = %s AND project_id = %s
                  AND aggregate_id = %s
                  AND event_type = 'parser.approved_snapshot.created'
                """,
                (
                    lifecycle["availableAt"],
                    lifecycle["updatedAt"],
                    lifecycle["publishedAt"],
                    json.dumps(lifecycle),
                    TENANT_A,
                    PROJECT_A,
                    snapshot["snapshotId"],
                ),
            )
            assert cursor.rowcount == 1
        admin.commit()
    finally:
        admin.close()

    replay = repository.append_record(TENANT_A, PROJECT_A, copy.deepcopy(snapshot))
    assert replay == first

    verification = live_database.connect_admin()
    try:
        with verification.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*), min(status), min(payload_json->>'status')
                FROM runtime_outbox_events
                WHERE tenant_id = %s AND project_id = %s
                  AND aggregate_id = %s
                  AND event_type = 'parser.approved_snapshot.created'
                """,
                (TENANT_A, PROJECT_A, snapshot["snapshotId"]),
            )
            assert cursor.fetchone() == (1, "published", "published")
    finally:
        verification.close()


def test_live_snapshot_membership_and_outbox_share_rollback_boundary(
    live_database: _LiveDatabase,
) -> None:
    import psycopg

    _seed_snapshot_authority(live_database)
    admin = live_database.connect_admin()
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                """
                CREATE FUNCTION parser_v2_live_fail_outbox()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $body$
                BEGIN
                    IF NEW.event_type = 'parser.approved_snapshot.created' THEN
                        RAISE EXCEPTION 'injected live outbox failure';
                    END IF;
                    RETURN NEW;
                END
                $body$
                """
            )
            cursor.execute(
                """
                CREATE TRIGGER parser_v2_live_fail_outbox_trigger
                BEFORE INSERT ON runtime_outbox_events
                FOR EACH ROW EXECUTE FUNCTION parser_v2_live_fail_outbox()
                """
            )
        admin.commit()

        repository = ParserV2PostgresRepository(live_database.connect_runtime)
        with pytest.raises(psycopg.Error, match="injected live outbox failure"):
            repository.append_record(TENANT_A, PROJECT_A, _snapshot_record())
    finally:
        admin.rollback()
        with admin.cursor() as cursor:
            cursor.execute(
                "DROP TRIGGER IF EXISTS parser_v2_live_fail_outbox_trigger "
                "ON runtime_outbox_events"
            )
            cursor.execute("DROP FUNCTION IF EXISTS parser_v2_live_fail_outbox()")
        admin.commit()
        admin.close()

    # Rollback proof inspects every internal membership table, including
    # INSERT-only tables intentionally hidden from the runtime role.
    verification = live_database.connect_admin()
    try:
        with verification.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('realytics.tenant_id', %s, true)",
                (TENANT_A,),
            )
            cursor.execute(
                """
                SELECT
                    (SELECT count(*) FROM parser_approved_snapshots
                     WHERE snapshot_id = 'parser-live-snapshot'),
                    (SELECT count(*) FROM parser_snapshot_selections
                     WHERE snapshot_id = 'parser-live-snapshot'),
                    (SELECT count(*) FROM parser_snapshot_source_runs
                     WHERE snapshot_id = 'parser-live-snapshot'),
                    (SELECT count(*) FROM runtime_outbox_events
                     WHERE aggregate_id = 'parser-live-snapshot'
                       AND event_type = 'parser.approved_snapshot.created')
                """
            )
            assert cursor.fetchone() == (0, 0, 0, 0)
    finally:
        verification.rollback()
        verification.close()
