"""Optional live PostgreSQL contract tests for normalized Parser V2.

Set ``REALYTICS_TEST_DATABASE_URL`` to an isolated, disposable database.  The
suite creates a uniquely named database plus a temporary unprivileged runtime role
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
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from db.repositories.postgres_project_repository import (
    PostgresProjectRepository,
)
from db.repositories.legal_intelligence_repository import (
    PostgresLegalTenantOverlayRepository,
)
from db.repositories.report_repository import PostgresReportRepository


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
                sql.SQL("SET search_path TO {}, extensions, public, pg_temp").format(
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


def _insert_live_legal_gate_case(
    cursor: Any,
    *,
    suffix: str,
    declared_count: int,
    item_has_provision: bool = True,
    version_verification_status: str = "schema_verified",
    version_has_verified_at: bool = True,
    fetch_status: str = "succeeded",
) -> dict[str, Any]:
    """Insert one complete legal lineage plus a deferred gate snapshot."""

    # Approval triggers deliberately reject approvals outside the database's
    # five-minute causal window. Use its clock rather than an expired fixture.
    cursor.execute("SELECT statement_timestamp()")
    database_clock = cursor.fetchone()[0]
    now = database_clock.isoformat()
    provider_id = f"legal-live-provider-{suffix}"
    source_fetch_id = f"legal-live-fetch-{suffix}"
    resource_id = f"legal-live-resource-{suffix}"
    version_id = f"legal-live-version-{suffix}"
    provision_id = f"legal-live-provision-{suffix}"
    snapshot_id = f"legal-live-snapshot-{suffix}"
    legal_review_id = f"legal-live-review-{suffix}"
    required_claim_set_id = f"legal-live-required-claims-{suffix}"
    snapshot_hash = _stable_hash(f"snapshot:{suffix}")
    claim_hash = _stable_hash(f"claim:{suffix}")
    claim_set_hash = hashlib.sha256(f"claim-1:{claim_hash}".encode("utf-8")).hexdigest()

    cursor.execute(
        "SELECT set_config('realytics.tenant_id', %s, true)",
        (TENANT_A,),
    )
    cursor.execute(
        "SELECT COALESCE(MAX(snapshot_version), 0) + 1 "
        "FROM legal_evidence_snapshots WHERE tenant_id = %s AND project_id = %s",
        (TENANT_A, PROJECT_A),
    )
    snapshot_version = int(cursor.fetchone()[0])
    cursor.execute(
        """
        INSERT INTO legal_source_providers(
            provider_id, provider_key, provider_revision, source_authority,
            authority_tier, provider_kind, canonical_base_url,
            adapter_version, provider_status, configured_by, configured_at
        ) VALUES (
            %s, %s, 1, 'law_go_kr', 'official_primary', 'official_api',
            'https://www.law.go.kr', 'live-test-v1', 'active',
            'live-test', %s
        )
        """,
        (provider_id, provider_id, now),
    )
    cursor.execute(
        """
        INSERT INTO legal_source_fetches(
            source_fetch_id, provider_id, transport, endpoint_key,
            sanitized_source_locator, request_fingerprint, response_status,
            fetch_status, raw_object_key, raw_object_version, raw_sha256,
            raw_size_bytes, fetched_at
        ) VALUES (
            %s, %s, 'direct_api', 'live-gate-test', %s, %s, %s,
            %s, %s, 'v1', %s, 1, %s
        )
        """,
        (
            source_fetch_id,
            provider_id,
            f"https://www.law.go.kr/live/{suffix}",
            _stable_hash(f"request:{suffix}"),
            200 if fetch_status == "succeeded" else 500,
            fetch_status,
            f"legal-live/{suffix}.json",
            _stable_hash(f"raw:{suffix}"),
            now,
        ),
    )
    cursor.execute(
        """
        INSERT INTO legal_resources(
            legal_resource_id, provider_id, resource_type,
            official_identifier, issuing_authority, identity_hash,
            first_observed_at
        ) VALUES (%s, %s, 'statute', %s, '법제처', %s, %s)
        """,
        (
            resource_id,
            provider_id,
            f"live-law-{suffix}",
            _stable_hash(f"identity:{suffix}"),
            now,
        ),
    )
    cursor.execute(
        """
        INSERT INTO legal_resource_versions(
            legal_resource_version_id, legal_resource_id, provider_id,
            source_fetch_id, official_version_identifier, version_sequence,
            canonical_title, observed_at, fetched_at, verified_at,
            verification_status, normalized_sha256, parser_schema_version
        ) VALUES (
            %s, %s, %s, %s, %s, 1, '라이브 법령', %s, %s, %s, %s, %s,
            'legal-parser-v1'
        )
        """,
        (
            version_id,
            resource_id,
            provider_id,
            source_fetch_id,
            f"live-version-{suffix}",
            now,
            now,
            now if version_has_verified_at else None,
            version_verification_status,
            _stable_hash(f"normalized:{suffix}"),
        ),
    )
    cursor.execute(
        """
        INSERT INTO legal_resource_version_lineages(
            legal_resource_version_lineage_id, legal_resource_id,
            legal_resource_version_id, lineage_type, continuity_status,
            validation_policy, validated_at
        ) VALUES (%s, %s, %s, 'initial', 'initial', 'not_applicable', %s)
        """,
        (f"legal-live-lineage-{suffix}", resource_id, version_id, now),
    )
    cursor.execute(
        """
        INSERT INTO legal_provisions(
            legal_provision_id, legal_resource_version_id, legal_resource_id,
            provision_type, provision_identifier, display_path, body_text,
            provision_sha256, source_locator
        ) VALUES (
            %s, %s, %s, 'article', '제1조', '제1조', '라이브 조문',
            %s, '제1조'
        )
        """,
        (
            provision_id,
            version_id,
            resource_id,
            hashlib.sha256("라이브 조문".encode("utf-8")).hexdigest(),
        ),
    )

    coverage = {
        "legalReviewId": legal_review_id,
        "coverageComplete": True,
        "expectedCitationCount": declared_count,
        "sealedCitationCount": declared_count,
        "claimCoverageComplete": True,
        "expectedClaimCount": declared_count,
        "verifiedClaimCount": declared_count,
    }
    draft_id = required_claim_set_id + ":draft"
    # The policy is append-only: review produces version 2 of a sealed draft.
    for set_id, version, status, predecessor, reviewer, role, approved_at in (
        (draft_id, 1, "draft", None, None, None, None),
        (required_claim_set_id, 2, "approved", draft_id,
         "live-policy-checker", "legal_counsel_approver", now),
    ):
        cursor.execute(
            """
            INSERT INTO legal_required_claim_sets(
                legal_required_claim_set_id, tenant_id, project_id,
                claim_set_key, claim_set_version, review_scope, status,
                claim_set_sha256, created_by, created_at,
                supersedes_claim_set_id, approved_by, approver_role,
                approved_at, metadata_json
            ) VALUES (
                %s, %s, %s, %s, %s, 'approval_gate', %s,
                %s, 'live-policy-maker', %s, %s, %s, %s, %s, '{}'::jsonb
            )
            """,
            (set_id, TENANT_A, PROJECT_A, f"live-required-claims-{suffix}",
             version, status, claim_set_hash, now, predecessor, reviewer, role, approved_at),
        )
        cursor.execute(
            """
            INSERT INTO legal_required_claims(
                legal_required_claim_id, tenant_id, project_id,
                legal_required_claim_set_id, claim_key,
                claim_text_sha256, ordinal, metadata_json
            ) VALUES (%s, %s, %s, %s, 'claim-1', %s, 1, '{}'::jsonb)
            """,
            (f"legal-live-required-claim-{suffix}-{version}",
             TENANT_A, PROJECT_A, set_id, claim_hash),
        )
    cursor.execute(
        """
        INSERT INTO legal_evidence_snapshots(
            legal_evidence_snapshot_id, tenant_id, project_id,
            snapshot_version, as_of_date, freshness_policy_version,
            snapshot_sha256, citation_verification_status,
            has_open_conflicts, is_stale, gate_eligible,
            legal_required_claim_set_id, required_claim_set_sha256,
            legal_review_id,
            created_by, created_at, metadata_json
        ) VALUES (
            %s, %s, %s, %s, %s, 'legal-freshness-v1',
            %s, 'verified', FALSE, FALSE, TRUE,
            %s, %s, %s, 'live-test', %s, %s::jsonb
        )
        """,
        (
            snapshot_id,
            TENANT_A,
            PROJECT_A,
            snapshot_version,
            database_clock.date(),
            snapshot_hash,
            required_claim_set_id,
            claim_set_hash,
            legal_review_id,
            now,
            json.dumps(coverage),
        ),
    )
    cursor.execute(
        """
        INSERT INTO legal_evidence_snapshot_items(
            legal_evidence_item_id, tenant_id, project_id,
            legal_evidence_snapshot_id, legal_resource_id,
            legal_resource_version_id, legal_provision_id, source_fetch_id,
            exact_locator, claim_text_sha256, excerpt_sha256,
            citation_verification_status, verified_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, '제1조',
            %s, %s, 'verified', %s
        )
        """,
        (
            f"legal-live-item-{suffix}",
            TENANT_A,
            PROJECT_A,
            snapshot_id,
            resource_id,
            version_id,
            provision_id if item_has_provision else None,
            source_fetch_id,
            claim_hash,
            _stable_hash(f"excerpt:{suffix}"),
            now,
        ),
    )
    projection_id = f"legal-live-review-projection-{suffix}"
    cursor.execute(
        """
        INSERT INTO project_legal_review_versions(
            review_version_id, tenant_id, project_id,
            review_id, review_version, status, review_level,
            evidence_status, evidence_locator_count,
            verified_evidence_count, verified_reference_count,
            verified_citation_count, required_claim_count,
            verified_claim_count, claim_coverage_complete,
            open_conflict_count, evidence_stale,
            citation_verification_status,
            legal_evidence_snapshot_id, snapshot_version,
            snapshot_sha256, snapshot_gate_eligible,
            required_claim_set_id, required_claim_set_sha256,
            parser_status, analysis_only, report_eligible,
            human_review_status, record_locator_json,
            evidence_json, parser_json, display_json,
            created_by, created_at
        ) VALUES (
            %s, %s, %s, %s, 1, 'caution', 'preliminary',
            'verified', 1, 1, 1, 1, 1, 1, TRUE,
            0, FALSE, 'verified',
            %s, %s, %s, TRUE, %s, %s,
            'not_applicable', TRUE, FALSE, 'pending',
            %s::jsonb, %s::jsonb, %s::jsonb, '{}'::jsonb,
            'live-legal-pod-runner', %s
        )
        RETURNING projection_sha256
        """,
        (
            projection_id,
            TENANT_A,
            PROJECT_A,
            legal_review_id,
            snapshot_id,
            snapshot_version,
            snapshot_hash,
            required_claim_set_id,
            claim_set_hash,
            json.dumps(
                {
                    "sourceType": "legal_review_record",
                    "reviewId": legal_review_id,
                }
            ),
            json.dumps(
                {
                    "status": "verified",
                    "evidenceLocators": [
                        {
                            "sourceType": "api_row",
                            "resourceId": resource_id,
                            "resourceVersionId": version_id,
                        }
                    ],
                }
            ),
            json.dumps({"status": "not_applicable"}),
            now,
        ),
    )
    projection_hash = str(cursor.fetchone()[0])
    return {
        "legalReviewId": legal_review_id,
        "snapshotId": snapshot_id,
        "snapshotVersion": snapshot_version,
        "snapshotSha256": snapshot_hash,
        "legalReviewProjectionId": projection_id,
        "legalReviewProjectionSha256": projection_hash,
    }


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
    from psycopg.conninfo import make_conninfo

    assert TEST_DSN is not None
    # Production repositories use the realytics schema. A second schema in the
    # same DB also collides with historical policy-name catalog checks. Exercise
    # the unchanged migration chain in a fresh database with its real namespace.
    schema_name = "realytics"
    database_name = _validated_identifier("parser_v2_live_db")
    candidate_role = _validated_identifier("parser_v2_runtime")
    runtime_role: str | None = None
    maintenance = _POSTGRES_CONNECT(TEST_DSN)
    maintenance.autocommit = True
    maintenance.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database_name)))
    isolated_dsn = make_conninfo(TEST_DSN, dbname=database_name)
    conn = postgres.connect(isolated_dsn)
    try:
        with conn.cursor() as cursor:
            cursor.execute("CREATE SCHEMA extensions")
            cursor.execute("CREATE EXTENSION postgis WITH SCHEMA extensions")
            cursor.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name))
            )
            cursor.execute(
                sql.SQL("SET search_path TO {}, extensions, public, pg_temp").format(
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
            dsn=isolated_dsn,
            schema_name=schema_name,
            runtime_role=runtime_role,
            first_migration_ledger=first_ledger,
            second_migration_ledger=second_ledger,
        )
        _seed_tenants_projects_and_profiles(conn)
        yield database
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
            try:
                maintenance.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
                if runtime_role:
                    maintenance.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(runtime_role)))
            finally:
                maintenance.close()


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
        required_rls_tables = [
            *postgres.PARSER_V2_RLS_TABLES,
            *postgres.REPORT_PUBLICATION_RLS_TABLES,
        ]
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = ANY(%s)
                ORDER BY c.relname
                """,
                (live_database.schema_name, required_rls_tables),
            )
            rows = cursor.fetchall()
        assert [row[0] for row in rows] == sorted(required_rls_tables)
        assert all(bool(row[1]) and bool(row[2]) for row in rows)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("table_name", "function_name", "values"),
    (
        ("project_legal_matters", "delegation", {"status": "open"}),
        ("project_legal_matters", "delegation", {"status": "resolved"}),
        ("legal_required_claim_sets", "delegation", {"status": "draft"}),
        ("legal_required_claim_sets", "delegation", {"status": "approved"}),
        ("legal_waivers", "delegation", {"status": "draft"}),
        ("counsel_opinions", "delegation", {"status": "draft"}),
        ("counsel_opinions", "delegation", {"status": "approved"}),
        ("legal_analysis_versions", "delegation", {"human_review_status": "pending"}),
        ("legal_analysis_versions", "delegation", {"human_review_status": "approved"}),
        ("project_legal_matters", "snapshot", {"status": "open"}),
        ("counsel_opinions", "snapshot", {"status": "draft"}),
        ("legal_analysis_versions", "snapshot", {"report_eligible": False}),
        (
            "legal_waivers", "snapshot",
            {"status": "draft", "conditions_json": "[]",
             "cure_condition_outcomes_json": "[]", "cure_status": "not_started"},
        ),
    ),
)
def test_live_governance_triggers_dispatch_before_resolving_table_columns(
    live_database: _LiveDatabase,
    table_name: str,
    function_name: str,
    values: dict[str, Any],
) -> None:
    from psycopg import sql

    conn = live_database.connect_admin()
    try:
        # Retain each real record shape while isolating this trigger from the
        # unrelated constraints covered by the complete governance tests below.
        conn.execute(sql.SQL(
            "CREATE TEMP TABLE {} ON COMMIT DROP AS SELECT * FROM {}.{} WITH NO DATA"
        ).format(sql.Identifier(table_name), sql.Identifier(live_database.schema_name),
                 sql.Identifier(table_name)))
        conn.execute(sql.SQL(
            "CREATE TRIGGER test_dispatch AFTER INSERT ON pg_temp.{} "
            "FOR EACH ROW EXECUTE FUNCTION {}.{}()"
        ).format(sql.Identifier(table_name), sql.Identifier(live_database.schema_name),
                 sql.Identifier(f"legal_assert_governance_{function_name}")))
        conn.execute(sql.SQL("INSERT INTO pg_temp.{} ({}) VALUES ({})").format(
            sql.Identifier(table_name),
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
        ), tuple(values.values()))
        assert conn.execute(sql.SQL("SELECT count(*) FROM pg_temp.{}").format(
            sql.Identifier(table_name),
        )).fetchone()[0] == 1
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize(
    "table_name", ("project_legal_review_versions", "project_permit_review_versions"),
)
@pytest.mark.parametrize("predecessor_case", ("valid", "missing", "other-tenant", "other-project"))
def test_live_review_projection_predecessor_lookup_enforces_scope(
    live_database: _LiveDatabase, table_name: str, predecessor_case: str,
) -> None:
    import psycopg
    from psycopg import sql

    conn = live_database.connect_admin()
    try:
        table = sql.Identifier(table_name)
        conn.execute(sql.SQL(
            "CREATE TEMP TABLE {} ON COMMIT DROP AS SELECT * FROM {}.{} WITH NO DATA"
        ).format(table, sql.Identifier(live_database.schema_name), table))
        if predecessor_case != "missing":
            conn.execute(sql.SQL(
                "INSERT INTO pg_temp.{} (review_version_id, tenant_id, project_id, "
                "review_id, review_version) VALUES ('prior', %s, %s, 'review', 1)"
            ).format(table), (
                "other" if predecessor_case == "other-tenant" else TENANT_A,
                "other" if predecessor_case == "other-project" else PROJECT_A,
            ))
        conn.execute(sql.SQL(
            "CREATE TRIGGER test_predecessor AFTER INSERT ON pg_temp.{} "
            "FOR EACH ROW EXECUTE FUNCTION {}.legal_assert_review_projection_snapshot()"
        ).format(table, sql.Identifier(live_database.schema_name)))
        insert = sql.SQL(
            "INSERT INTO pg_temp.{} (review_version_id, tenant_id, project_id, "
            "review_id, review_version, supersedes_review_version_id) "
            "VALUES ('next', %s, %s, 'review', 2, 'prior')"
        ).format(table)
        if predecessor_case == "valid":
            conn.execute(insert, (TENANT_A, PROJECT_A))
            assert conn.execute(sql.SQL("SELECT count(*) FROM pg_temp.{}").format(
                table,
            )).fetchone()[0] == 2
        else:
            with pytest.raises(psycopg.Error, match="predecessor is outside") as exc_info:
                conn.execute(insert, (TENANT_A, PROJECT_A))
            assert exc_info.value.sqlstate == "P0001"
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize(
    (
        "declared_count",
        "item_has_provision",
        "version_verification_status",
        "version_has_verified_at",
        "fetch_status",
        "expected_constraint",
    ),
    (
        pytest.param(
            1,
            True,
            "schema_verified",
            True,
            "succeeded",
            None,
            id="verified-exact-provision",
        ),
        pytest.param(
            5,
            True,
            "schema_verified",
            True,
            "succeeded",
            "ck_legal_evidence_snapshot_gate_item_counts",
            id="inflated-coverage",
        ),
        pytest.param(
            1,
            False,
            "schema_verified",
            True,
            "succeeded",
            "ck_legal_evidence_snapshot_gate_lineage",
            id="resource-level-item",
        ),
        pytest.param(
            1,
            True,
            "unverified",
            False,
            "succeeded",
            "ck_legal_evidence_snapshot_gate_lineage",
            id="unverified-version",
        ),
        pytest.param(
            1,
            True,
            "schema_verified",
            False,
            "succeeded",
            "ck_legal_evidence_snapshot_gate_lineage",
            id="missing-version-verification-time",
        ),
        pytest.param(
            1,
            True,
            "schema_verified",
            True,
            "failed",
            "ck_legal_evidence_snapshot_gate_lineage",
            id="failed-source-fetch",
        ),
    ),
)
def test_live_gate_snapshot_reconciles_counts_and_corpus_lineage(
    live_database: _LiveDatabase,
    declared_count: int,
    item_has_provision: bool,
    version_verification_status: str,
    version_has_verified_at: bool,
    fetch_status: str,
    expected_constraint: str | None,
) -> None:
    import psycopg

    conn = live_database.connect_admin()
    try:
        suffix = uuid4().hex
        with conn.cursor() as cursor:
            _insert_live_legal_gate_case(
                cursor,
                suffix=suffix,
                declared_count=declared_count,
                item_has_provision=item_has_provision,
                version_verification_status=version_verification_status,
                version_has_verified_at=version_has_verified_at,
                fetch_status=fetch_status,
            )

        if expected_constraint is None:
            conn.commit()
        else:
            with pytest.raises(psycopg.Error) as exc_info:
                conn.commit()
            assert exc_info.value.sqlstate == "23514"
            assert exc_info.value.diag.constraint_name == expected_constraint
    finally:
        conn.rollback()
        conn.close()


def test_live_normalized_approval_readiness_rechecks_snapshot_and_decision(
    live_database: _LiveDatabase,
) -> None:
    conn = live_database.connect_admin()
    try:
        delegation_created_at = conn.execute("SELECT statement_timestamp()").fetchone()[0]
        delegation_valid_from = delegation_created_at
        delegation_valid_until = delegation_created_at + timedelta(days=1)
        suffix = uuid4().hex
        with conn.cursor() as cursor:
            case = _insert_live_legal_gate_case(
                cursor,
                suffix=suffix,
                declared_count=1,
            )
            delegation_id = f"legal-live-delegation-{suffix}:v1"
            cursor.execute(
                """
                INSERT INTO legal_approval_delegations (
                    legal_approval_delegation_id,
                    tenant_id, project_id, delegation_key,
                    delegation_version, grantor_id, grantor_role,
                    grantee_id, grantee_role, scope,
                    valid_from, valid_until, status, reason,
                    supersedes_delegation_id, created_at, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, 1,
                    'live-legal-approver',
                    'legal_counsel_approver',
                    'live-external-counsel',
                    'external_counsel',
                    'approval_gate',
                    %s,
                    %s,
                    'active',
                    'Live external counsel delegation.',
                    NULL,
                    %s,
                    '{}'::jsonb
                )
                """,
                (
                    delegation_id,
                    TENANT_A,
                    PROJECT_A,
                    f"legal-live-delegation-{suffix}",
                    delegation_valid_from,
                    delegation_valid_until,
                    delegation_created_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # The decision must occur after the evidence/projection was persisted.
    clock_connection = live_database.connect_admin()
    try:
        decision_clock = clock_connection.execute("SELECT statement_timestamp()").fetchone()[0]
    finally:
        clock_connection.close()
    repository = PostgresLegalTenantOverlayRepository(
        tenant_id=TENANT_A,
        project_id=PROJECT_A,
        connection_factory=live_database.connect_admin,
        schema=live_database.schema_name,
    )
    repository.append_review_decision(
        {
            "decisionId": f"legal-live-decision-{suffix}",
            "decisionType": "approve",
            "decisionScope": "approval_gate",
            "reason": "Live normalized readiness contract test.",
            "reviewerId": "live-external-counsel",
            "reviewerRole": "external_counsel",
            "legalApprovalDelegationId": delegation_id,
            "decidedAt": decision_clock.isoformat(),
            "legalEvidenceSnapshotId": case["snapshotId"],
            "legalReviewProjectionId": case[
                "legalReviewProjectionId"
            ],
            "legalReviewProjectionVersion": 1,
            "legalReviewProjectionSha256": (
                f"sha256:{case['legalReviewProjectionSha256']}"
            ),
            "metadata": {
                "legalReviewId": case["legalReviewId"],
                "evidenceSnapshotVersion": case["snapshotVersion"],
                "evidenceSnapshotSha256": (
                    f"sha256:{case['snapshotSha256']}"
                ),
                "legalReviewProjectionId": case[
                    "legalReviewProjectionId"
                ],
                "legalReviewProjectionVersion": 1,
                "legalReviewProjectionSha256": (
                    f"sha256:{case['legalReviewProjectionSha256']}"
                ),
            },
        }
    )

    ready = repository.evaluate_approval_readiness(
        legal_review_id=case["legalReviewId"],
        evidence_snapshot_id=case["snapshotId"],
        expected_snapshot_version=case["snapshotVersion"],
        expected_snapshot_sha256=f"sha256:{case['snapshotSha256']}",
    )
    changed_identity = repository.evaluate_approval_readiness(
        legal_review_id=case["legalReviewId"],
        evidence_snapshot_id=case["snapshotId"],
        expected_snapshot_version=case["snapshotVersion"],
        expected_snapshot_sha256="sha256:" + ("f" * 64),
    )

    assert ready["pass"] is True, json.dumps(ready, sort_keys=True, default=str)
    assert ready["source"] == "normalized_postgres"
    assert ready["lineageValid"] is True
    assert ready["legalReviewProjectionId"] == (
        case["legalReviewProjectionId"]
    )
    assert ready["legalReviewProjectionSha256"] == (
        f"sha256:{case['legalReviewProjectionSha256']}"
    )
    assert ready["humanReviewStatus"] == "counsel_approved"
    assert changed_identity["pass"] is False
    assert changed_identity["reason"] == "snapshot_identity_mismatch"


def test_live_successful_source_fetch_requires_immutable_object_version(
    live_database: _LiveDatabase,
) -> None:
    import psycopg

    conn = live_database.connect_admin()
    try:
        suffix = uuid4().hex
        provider_id = f"legal-live-version-provider-{suffix}"
        now = "2026-07-18T00:00:00+00:00"
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO legal_source_providers(
                    provider_id, provider_key, provider_revision,
                    source_authority, authority_tier, provider_kind,
                    canonical_base_url, adapter_version, provider_status,
                    configured_by, configured_at
                ) VALUES (
                    %s, %s, 1, 'law_go_kr', 'official_primary',
                    'official_api', 'https://www.law.go.kr', 'live-test-v1',
                    'active', 'live-test', %s
                )
                """,
                (provider_id, provider_id, now),
            )
            with pytest.raises(psycopg.Error) as exc_info:
                cursor.execute(
                    """
                    INSERT INTO legal_source_fetches(
                        source_fetch_id, provider_id, transport, endpoint_key,
                        sanitized_source_locator, request_fingerprint,
                        response_status, fetch_status, raw_object_key,
                        raw_object_version, raw_sha256, raw_size_bytes,
                        fetched_at
                    ) VALUES (
                        %s, %s, 'direct_api', 'live-object-version-test',
                        %s, %s, 200, 'succeeded', %s, NULL, %s, 1, %s
                    )
                    """,
                    (
                        f"legal-live-version-fetch-{suffix}",
                        provider_id,
                        f"https://www.law.go.kr/live/version/{suffix}",
                        _stable_hash(f"version-request:{suffix}"),
                        f"legal-live/version/{suffix}.json",
                        _stable_hash(f"version-raw:{suffix}"),
                        now,
                    ),
                )
        assert exc_info.value.sqlstate == "23514"
        assert exc_info.value.diag.constraint_name in {
            "ck_legal_source_fetch_raw_object_tuple",
            "ck_legal_source_fetch_success_has_immutable_object",
        }
    finally:
        conn.rollback()
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


def test_live_pf_worker_login_grants_and_forbidden_approval_writes(
    live_database: _LiveDatabase,
) -> None:
    """Rehearse a real direct LOGIN only inside the disposable test database."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from db.postgres_pf_worker_privileges import render_pf_worker_role_sql
    from db.postgres_pf_worker_runtime_preflight import (
        assert_pf_worker_runtime_connection, PFWorkerRuntimePreflightError,
    )
    role = _validated_identifier("pf_worker_live")
    password = uuid4().hex
    admin = live_database.connect_admin()
    worker = None
    created = False
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(role), sql.Literal(password)))
            cursor.execute("SELECT current_database()")
            for statement in render_pf_worker_role_sql(
                role_name=role, schema_name=live_database.schema_name,
                database_name=cursor.fetchone()[0],
            ):
                cursor.execute(statement)
        admin.commit()
        created = True
        worker = _POSTGRES_CONNECT(make_conninfo(live_database.dsn, user=role, password=password))
        assert_pf_worker_runtime_connection(worker, expected_role=role)
        worker.rollback()

        with worker.cursor() as cursor:
            cursor.execute("SELECT set_config('realytics.tenant_id', %s, true)", (TENANT_A,))
            cursor.execute("SELECT project_id FROM realytics.projects WHERE project_id = %s", (PROJECT_A,))
            assert cursor.fetchone() == (PROJECT_A,)
            cursor.execute("SELECT count(*) FROM realytics.projects WHERE project_id = %s", (PROJECT_B,))
            assert cursor.fetchone() == (0,)
        worker.rollback()
        for table in (
            "report_publications", "parser_review_decisions", "tenant_memberships",
            "projects", "calculation_runs",
        ):
            with pytest.raises(psycopg.Error) as denied:
                worker.execute(sql.SQL("INSERT INTO realytics.{} DEFAULT VALUES").format(sql.Identifier(table)))
            assert denied.value.sqlstate == "42501"
            worker.rollback()

        # A column-only identity grant must fail the same effective audit even
        # though has_table_privilege(..., 'UPDATE') remains false.
        admin.execute(sql.SQL("GRANT UPDATE (role) ON realytics.tenant_memberships TO {}").format(sql.Identifier(role)))
        admin.commit()
        with pytest.raises(PFWorkerRuntimePreflightError, match="pf_worker_forbidden_runtime_privilege"):
            assert_pf_worker_runtime_connection(worker, expected_role=role)
        worker.rollback()
        admin.execute(sql.SQL("REVOKE UPDATE (role) ON realytics.tenant_memberships FROM {}").format(sql.Identifier(role)))
        admin.commit()

        # An unlisted private table and a future column grant cannot escape
        # the catalog-based deny check. Reprovisioning resets only this role.
        unlisted_table = _validated_identifier("pf_unlisted")
        admin.execute(sql.SQL("CREATE TABLE realytics.{} (payload text)").format(sql.Identifier(unlisted_table)))
        admin.execute(sql.SQL("GRANT SELECT (payload) ON realytics.{} TO {}").format(sql.Identifier(unlisted_table), sql.Identifier(role)))
        admin.commit()
        with pytest.raises(PFWorkerRuntimePreflightError, match="pf_worker_forbidden_runtime_privilege"):
            assert_pf_worker_runtime_connection(worker, expected_role=role)
        worker.rollback()
        database_name = admin.execute("SELECT current_database()").fetchone()[0]
        for statement in render_pf_worker_role_sql(role_name=role, database_name=database_name):
            admin.execute(statement)
        admin.commit()
        assert_pf_worker_runtime_connection(worker, expected_role=role)
        worker.rollback()
        # PUBLIC is intentionally not mutated by the renderer; a shared leak
        # must still be rejected as an effective grant.
        admin.execute(sql.SQL("GRANT SELECT ON realytics.{} TO PUBLIC").format(sql.Identifier(unlisted_table)))
        admin.commit()
        with pytest.raises(PFWorkerRuntimePreflightError, match="pf_worker_forbidden_runtime_privilege"):
            assert_pf_worker_runtime_connection(worker, expected_role=role)
        worker.rollback()
        admin.execute(sql.SQL("DROP TABLE realytics.{}").format(sql.Identifier(unlisted_table)))
        admin.commit()
    finally:
        if worker is not None:
            worker.close()
        admin.rollback()
        if created:
            admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            admin.commit()
        admin.close()


def test_live_runtime_project_create_update_and_soft_delete(
    live_database: _LiveDatabase,
) -> None:
    @contextmanager
    def transaction(tenant_id: str):
        connection = live_database.connect_runtime()
        try:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('realytics.tenant_id', %s, true)",
                    (tenant_id,),
                )
                yield connection
        finally:
            connection.close()

    repository = PostgresProjectRepository(
        tenant_id=TENANT_A,
        transaction_factory=transaction,
    )
    created = repository.create(
        {"project": {"name": "Runtime privilege contract"}},
        created_by="live-runtime-test",
    )
    project_id = created["project"]["id"]

    assert created["_meta"]["tenantId"] == TENANT_A
    assert created["_meta"]["stateVersion"] == 1

    updated = repository.update(
        project_id,
        {"project": {"status": "reviewing"}},
        expected_state_version=1,
    )
    assert updated is not None
    assert updated["project"]["status"] == "reviewing"
    assert updated["_meta"]["stateVersion"] == 2

    deleted = repository.delete(
        project_id,
        expected_state_version=2,
    )
    assert deleted is not None
    assert repository.get(project_id) is None


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


def test_live_concurrent_report_publication_is_single_and_idempotent(
    live_database: _LiveDatabase,
) -> None:
    """Exercise the real unique key, row lock, RLS, and runtime grants.

    The module fixture owns a disposable schema and drops it after the suite,
    so the immutable publication row never leaks into a developer database.
    """

    suffix = uuid4().hex
    run_id = f"report-live-run-{suffix}"
    snapshot_id = f"report-live-snapshot-{suffix}"
    snapshot_hash = "sha256:" + ("a" * 64)
    now = "2026-07-27T00:00:00+00:00"

    admin = live_database.connect_admin()
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('realytics.tenant_id', %s, true)",
                (TENANT_A,),
            )
            cursor.execute(
                """
                INSERT INTO orchestrations (
                    tenant_id, project_id, run_id, status, started_at,
                    completed_at, payload_json
                ) VALUES (
                    %s, %s, %s, 'completed', %s, %s, %s::jsonb
                )
                """,
                (
                    TENANT_A,
                    PROJECT_A,
                    run_id,
                    now,
                    now,
                    json.dumps(
                        {
                            "project_id": PROJECT_A,
                            "run_id": run_id,
                            "approvalStatus": "approved",
                            "user_id": "live-maker",
                            "approval": {
                                "approved_by": "live-approval-checker",
                                "approved_at": now,
                            },
                        }
                    ),
                ),
            )
            cursor.execute(
                """
                INSERT INTO project_snapshots (
                    tenant_id, project_id, snapshot_id, run_id, created_at,
                    payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    TENANT_A,
                    PROJECT_A,
                    snapshot_id,
                    run_id,
                    now,
                    json.dumps(
                        {
                            "projectId": PROJECT_A,
                            "runId": run_id,
                            "snapshotId": snapshot_id,
                            "snapshotHash": snapshot_hash,
                        }
                    ),
                ),
            )
        admin.commit()
    finally:
        admin.close()

    @contextmanager
    def transaction(tenant_id: str):
        connection = live_database.connect_runtime()
        try:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('realytics.tenant_id', %s, true)",
                    (tenant_id,),
                )
                yield connection
        finally:
            connection.close()

    repository = PostgresReportRepository(
        tenant_id=TENANT_A,
        transaction_factory=transaction,
    )
    report = repository.create(
        PROJECT_A,
        {
            "template": "phase-11-pf-review",
            "title": "Live publication",
            "qualityChecks": {
                "actualSectionCount": 1,
                "expectedSectionCount": 1,
                "isComplete": True,
            },
            "sections": {"cover": "verified"},
        },
        "# live report",
        run_id=run_id,
    )
    read_token = repository.get(PROJECT_A, report["report_id"])
    assert read_token is not None
    decision_package = {
        "schemaVersion": "realytics-decision-package-manifest-v1",
        "projectId": PROJECT_A,
        "projectSnapshotId": snapshot_id,
        "projectSnapshotHash": snapshot_hash,
        "orchestrationRunId": run_id,
        "approvalStatus": "approved",
        "approvedBy": "live-approval-checker",
    }

    def publish(actor: str):
        return repository.publish(
            copy.deepcopy(read_token),
            published_by=actor,
            published_at="2026-07-27T01:00:00+00:00",
            decision_package=decision_package,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(publish, ("live-checker-a", "live-checker-b"))
        )

    assert {result["status"] for result in results} == {"published"}
    assert len(
        {
            result["publication"]["idempotencyKey"]
            for result in results
        }
    ) == 1
    assert len(
        {
            result["publication"]["publishedBy"]
            for result in results
        }
    ) == 1

    verification = live_database.connect_admin()
    try:
        with verification.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('realytics.tenant_id', %s, true)",
                (TENANT_A,),
            )
            cursor.execute(
                """
                SELECT count(*), min(content_sha256), min(idempotency_key)
                FROM report_publications
                WHERE tenant_id = %s
                  AND project_id = %s
                  AND report_id = %s
                """,
                (TENANT_A, PROJECT_A, report["report_id"]),
            )
            count, content_sha256, idempotency_key = cursor.fetchone()
        assert count == 1
        assert str(content_sha256).startswith("sha256:")
        assert str(idempotency_key).startswith("sha256:")
    finally:
        verification.rollback()
        verification.close()

    with pytest.raises(Exception, match="published report is immutable"):
        with transaction(TENANT_A) as connection:
            connection.execute(
                """
                UPDATE reports
                SET payload_json = jsonb_set(
                    payload_json,
                    '{report_markdown}',
                    '"tampered"'::jsonb
                )
                WHERE tenant_id = %s
                  AND project_id = %s
                  AND report_id = %s
                """,
                (TENANT_A, PROJECT_A, report["report_id"]),
            )


@pytest.mark.parametrize("report_kind", ["comparison", "pf_cashflow"])
def test_live_frozen_report_round_trip_export_and_tenant_integrity(
    live_database: _LiveDatabase, report_kind: str,
) -> None:
    """Use the disposable CI database and runtime role, not the repository double."""
    from agents.report_export_agent import ReportExportAgent
    from db.repositories.report_repository import ReportPublicationConflict
    from test_cash_flow_comparison import record
    from use_cases.cash_flow_comparison import compare_cash_flow_runs, content_hash
    from use_cases.report_generation import generate_and_save_cash_flow_comparison_report_use_case
    from agents.report_agent import ReportAgent
    from use_cases.report_calculation_basis import attach_cash_flow_basis

    @contextmanager
    def transaction(tenant_id: str):
        connection = live_database.connect_runtime()
        try:
            with connection.transaction():
                connection.execute("SELECT set_config('realytics.tenant_id', %s, true)", (tenant_id,))
                yield connection
        finally:
            connection.close()

    repository = PostgresReportRepository(tenant_id=TENANT_A, transaction_factory=transaction)
    baseline = record("comparison-live-baseline", project_id=PROJECT_A, tenant=TENANT_A)
    changed = record("comparison-live-changed", project_id=PROJECT_A, tenant=TENANT_A, construction=500)
    compared = compare_cash_flow_runs(baseline, changed)
    selection = {"baselineRunId": baseline["calculationRunId"], "comparisonRunId": changed["calculationRunId"],
        "savedAt": "2026-10-14T00:00:00Z", "savedBy": "ci-comparison-reviewer"}
    for side in ("baseline", "comparison"):
        selection[side + "InputHash"] = compared[side]["inputHash"]
        selection[side + "ResultHash"] = compared[side]["resultHash"]
    if report_kind == "comparison":
        report = generate_and_save_cash_flow_comparison_report_use_case(repository, PROJECT_A, TENANT_A,
            selection, content_hash(selection), baseline, changed, captured_by="ci-comparison-reviewer")["reportRecord"]
        snapshot_key = "cashFlowComparisonSnapshot"
    else:
        structured = ReportAgent().generate_structured_report({})
        attach_cash_flow_basis(structured, changed)
        markdown = ReportExportAgent().export({"report": structured}, "markdown")["content"]
        report = repository.create(PROJECT_A, structured, markdown)
        snapshot_key = "cashFlowCalculationSnapshot"
    saved_snapshot = copy.deepcopy(report["report"][snapshot_key])
    changed["resultSnapshot"]["summary"]["totalRevenue"] = 1
    reopened = repository.get(PROJECT_A, report["report_id"])
    assert reopened is not None
    assert reopened["report"][snapshot_key] == saved_snapshot
    assert reopened["status"] == "draft" and reopened["run_id"] is None
    assert reopened["metadata"]["report_eligible"] is False
    exported = repository.add_export(reopened, ReportExportAgent().export(reopened, "pdf"))
    reopened = repository.get(PROJECT_A, report["report_id"])
    assert repository.get_export(reopened, exported["export_id"]) == exported
    foreign = PostgresReportRepository(tenant_id=TENANT_B, transaction_factory=transaction)
    assert foreign.get(PROJECT_A, report["report_id"]) is None
    assert repository.get(PROJECT_B, report["report_id"]) is None
    with pytest.raises(ReportPublicationConflict):
        repository.publish(reopened, published_by="ci-checker", published_at="2026-10-14T01:00:00Z", decision_package={})
    with transaction(TENANT_A) as connection:
        connection.execute("""UPDATE realytics.reports
            SET payload_json = jsonb_set(payload_json, '{report_markdown}', '"tampered"'::jsonb)
            WHERE tenant_id = %s AND project_id = %s AND report_id = %s""",
            (TENANT_A, PROJECT_A, report["report_id"]))
    with pytest.raises(ReportPublicationConflict):
        repository.get(PROJECT_A, report["report_id"])
