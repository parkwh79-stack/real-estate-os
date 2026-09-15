from __future__ import annotations

"""Least-privilege PostgreSQL grants for normalized runtime domains.

The role name is deployment-specific, so these grants deliberately do not live
in a numbered schema migration.  The deployment runner applies this manifest
after migrations and before the API readiness probe.
"""

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any


RUNTIME_ROLE_ENV = "REALYTICS_RUNTIME_DATABASE_ROLE"
RUNTIME_SCHEMA_ENV = "REALYTICS_DATABASE_SCHEMA"
POSTGIS_SCHEMA_ENV = "REALYTICS_POSTGIS_SCHEMA"
REVOKE_PUBLIC_DATABASE_TEMPORARY_ENV = (
    "REALYTICS_REVOKE_PUBLIC_DATABASE_TEMPORARY"
)
DEFAULT_RUNTIME_SCHEMA = "public"
DEFAULT_POSTGIS_SCHEMA = "extensions"

# Parser tables are owned by the migration role and are append-only.  The
# runtime role receives only the operations exercised by the normalized
# repository.  Empty tuples are intentional: the table is RLS-protected and
# reserved for a future governed workflow, but the current runtime does not use
# it yet.
PARSER_V2_TABLE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "parser_document_versions": ("SELECT", "INSERT"),
    "parser_project_profiles": ("SELECT", "INSERT"),
    "parser_runs": ("SELECT", "INSERT"),
    "parser_run_events": ("SELECT", "INSERT"),
    "parser_candidate_set_seals": ("SELECT", "INSERT"),
    "parsed_fact_candidates": ("SELECT", "INSERT"),
    "parser_candidate_inputs": ("SELECT", "INSERT"),
    "parser_validation_findings": ("INSERT",),
    "parser_validation_finding_events": (),
    "parser_review_decisions": ("SELECT", "INSERT"),
    "parser_source_conflicts": ("SELECT", "INSERT"),
    "parser_source_conflict_events": ("SELECT", "INSERT"),
    "parser_conflict_candidates": ("SELECT", "INSERT"),
    "parser_review_decision_conflicts": ("INSERT",),
    "parser_canonical_selections": ("SELECT", "INSERT"),
    "parser_approved_snapshots": ("SELECT", "INSERT"),
    "parser_snapshot_selections": ("SELECT", "INSERT"),
    "parser_snapshot_source_runs": ("INSERT",),
    "parser_validity_events": ("SELECT", "INSERT"),
}

# Project creation and editing are served by ``PostgresProjectRepository``.
# DELETE is intentionally absent because the repository implements deletion as
# a tenant-scoped, optimistic-concurrency UPDATE tombstone.  Keeping this
# allowlist exact prevents a UI-level ``project:write`` grant from being
# defeated by a missing database privilege while preserving FORCE RLS as the
# row authorization boundary.
PROJECT_RUNTIME_TABLE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "projects": ("SELECT", "INSERT", "UPDATE"),
    # Recent-work is derived from append-only server-observed request history.
    # The serving API can read and append, but never rewrite or delete events.
    "user_activity_logs": ("SELECT", "INSERT"),
}
PROJECT_RUNTIME_SEQUENCE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    # ``nextval`` needs USAGE (or the broader UPDATE privilege).  USAGE is the
    # narrow choice and does not allow callers to reset the sequence with
    # ``setval``.
    "project_id_sequence": ("USAGE",),
}

# Tenant membership administration runs through the tenant-scoped API pool,
# never the read-only identity pool.  The directory relations intentionally do
# not receive broad table SELECT/UPDATE grants: only the columns projected by
# the authorized membership screen and changed by the optimistic-CAS command
# are visible to the serving role.  Audit history can only be appended.
MEMBERSHIP_ADMIN_TABLE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
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
MEMBERSHIP_ADMIN_COLUMN_PRIVILEGES: Mapping[
    str, Mapping[str, tuple[str, ...]]
] = {
    "principals": {
        "SELECT": (
            "issuer",
            "subject",
            "principal_type",
            "status",
            "email",
            "display_name",
        ),
        "INSERT": (
            "issuer",
            "subject",
            "principal_type",
            "status",
            "email",
            "display_name",
            "verification_method",
            "first_verified_at",
            "last_verified_at",
            "created_at",
            "updated_at",
        ),
        "UPDATE": ("email", "display_name", "updated_at"),
    },
    "tenant_memberships": {
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
    },
    "tenants": {
        "SELECT": ("tenant_id", "name", "status"),
        "INSERT": (
            "tenant_id",
            "name",
            "status",
            "created_at",
            "updated_at",
            "metadata_json",
        ),
    },
    # Self-owned onboarding profiles/events use the separately provisioned
    # identity role. The API review role receives no column grant here.
    "onboarding_profiles": {},
    "onboarding_principal_events": {},
    "platform_onboarding_operators": {
        "SELECT": ("issuer", "subject", "status", "operator_version"),
    },
    "tenant_access_requests": {
        "SELECT": (
            "request_id",
            "request_type",
            "requester_issuer",
            "requester_subject",
            "requester_email_snapshot",
            "requester_display_name_snapshot",
            "profile_version_snapshot",
            "terms_version_snapshot",
            "privacy_version_snapshot",
            "profile_snapshot_hash",
            "target_tenant_id",
            "organization_name",
            "business_registration_number",
            "requested_role",
            "request_message",
            "status",
            "request_version",
            "submitted_at",
            "decision_by_issuer",
            "decision_by_subject",
            "decision_at",
            "decision_reason",
            "approved_tenant_id",
            "created_at",
            "updated_at",
        ),
        "UPDATE": (
            "status",
            "request_version",
            "decision_by_issuer",
            "decision_by_subject",
            "decision_at",
            "decision_reason",
            "approved_tenant_id",
            "updated_at",
        ),
    },
    "tenant_access_request_events": {
        "INSERT": (
            "event_id",
            "request_id",
            "event_type",
            "request_type",
            "requester_issuer",
            "requester_subject",
            "target_tenant_id",
            "actor_issuer",
            "actor_subject",
            "event_payload",
            "occurred_at",
        ),
    },
}
MEMBERSHIP_ADMIN_FORBIDDEN_COLUMN_PRIVILEGES: Mapping[
    str, Mapping[str, tuple[str, ...]]
] = {
    "principals": {
        "SELECT": (
            "legacy_user_id",
            "verification_method",
            "first_verified_at",
            "last_verified_at",
            "created_at",
            "updated_at",
        ),
        "UPDATE": (
            "issuer",
            "subject",
            "principal_type",
            "status",
            "legacy_user_id",
            "verification_method",
            "first_verified_at",
            "last_verified_at",
            "created_at",
        ),
    },
    "tenant_memberships": {
        "SELECT": ("membership_source", "created_at"),
        "UPDATE": (
            "tenant_id",
            "issuer",
            "subject",
            "status",
            "membership_source",
            "created_at",
        ),
    },
    "tenants": {
        "SELECT": ("created_at", "updated_at", "metadata_json"),
        "UPDATE": (
            "tenant_id",
            "name",
            "status",
            "created_at",
            "updated_at",
            "metadata_json",
        ),
    },
    "onboarding_profiles": {
        operation: (
            "issuer",
            "subject",
            "contact_email",
            "display_name",
            "phone",
            "job_title",
            "terms_version",
            "privacy_version",
            "terms_accepted_at",
            "privacy_accepted_at",
            "profile_version",
            "created_at",
            "updated_at",
        )
        for operation in ("SELECT", "INSERT", "UPDATE")
    },
    "onboarding_principal_events": {
        operation: (
            "event_id",
            "issuer",
            "subject",
            "event_type",
            "event_payload",
            "occurred_at",
            "created_at",
        )
        for operation in ("SELECT", "INSERT", "UPDATE")
    },
    "platform_onboarding_operators": {
        "SELECT": ("created_at", "updated_at"),
        **{
            operation: (
                "issuer",
                "subject",
                "status",
                "operator_version",
                "created_at",
                "updated_at",
            )
            for operation in ("INSERT", "UPDATE")
        },
    },
    "tenant_access_requests": {
        "INSERT": (
            "request_id",
            "request_type",
            "requester_issuer",
            "requester_subject",
            "requester_email_snapshot",
            "requester_display_name_snapshot",
            "profile_version_snapshot",
            "terms_version_snapshot",
            "privacy_version_snapshot",
            "profile_snapshot_hash",
            "target_tenant_id",
            "organization_name",
            "business_registration_number",
            "requested_role",
            "request_message",
            "status",
            "request_version",
            "submitted_at",
            "decision_by_issuer",
            "decision_by_subject",
            "decision_at",
            "decision_reason",
            "approved_tenant_id",
            "created_at",
            "updated_at",
        ),
        "UPDATE": (
            "request_id",
            "request_type",
            "requester_issuer",
            "requester_subject",
            "requester_email_snapshot",
            "requester_display_name_snapshot",
            "profile_version_snapshot",
            "terms_version_snapshot",
            "privacy_version_snapshot",
            "profile_snapshot_hash",
            "target_tenant_id",
            "organization_name",
            "business_registration_number",
            "requested_role",
            "request_message",
            "submitted_at",
            "created_at",
        ),
    },
    "tenant_access_request_events": {
        "SELECT": (
            "event_id",
            "request_id",
            "event_type",
            "request_type",
            "requester_issuer",
            "requester_subject",
            "target_tenant_id",
            "actor_issuer",
            "actor_subject",
            "event_payload",
            "occurred_at",
            "created_at",
        ),
        "UPDATE": (
            "event_id",
            "request_id",
            "event_type",
            "request_type",
            "requester_issuer",
            "requester_subject",
            "target_tenant_id",
            "actor_issuer",
            "actor_subject",
            "event_payload",
            "occurred_at",
            "created_at",
        ),
        "INSERT": ("created_at",),
    },
}
MEMBERSHIP_ADMIN_FORBIDDEN_TABLE_PRIVILEGES: Mapping[
    str, tuple[str, ...]
] = {
    "principals": (
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ),
    "tenant_memberships": (
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ),
    "tenant_identity_access_events": (
        "SELECT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ),
    **{
        table: (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        )
        for table in (
            "tenants",
            "onboarding_profiles",
            "onboarding_principal_events",
            "platform_onboarding_operators",
            "tenant_access_requests",
            "tenant_access_request_events",
        )
    },
}

# Reserved for additive support contracts. Calculation writes now have an
# exact, managed contract below, including forbidden destructive privileges.
PARSER_V2_SUPPORT_PRIVILEGES: Mapping[str, tuple[str, ...]] = {}

# These objects have a complete, known runtime contract and can be managed
# exactly.  The outbox dispatcher needs INSERT for idempotent enqueue, SELECT for
# claim/read, and UPDATE for lease/retry/published transitions.
PARSER_V2_MANAGED_SUPPORT_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "schema_migrations": ("SELECT",),
    "runtime_outbox_events": ("SELECT", "INSERT", "UPDATE"),
    "calculation_runs": ("SELECT", "INSERT", "UPDATE"),
    "deal_state_records": ("SELECT", "INSERT", "UPDATE"),
    "chief_pod_artifacts": ("SELECT", "INSERT", "UPDATE"),
    "pod_ai_reviews": ("SELECT", "INSERT", "UPDATE"),
    "agent_handoffs": ("SELECT", "INSERT", "UPDATE"),
    "recalculation_events": ("SELECT", "INSERT", "UPDATE"),
    "assumption_overrides": ("SELECT",),
    "async_jobs": ("SELECT", "INSERT", "UPDATE"),
    "agent_runs": ("SELECT", "INSERT", "UPDATE"),
    "agent_run_steps": ("SELECT", "INSERT", "UPDATE"),
    "brain_usage_events": ("SELECT", "INSERT", "UPDATE"),
}

# Final report issuance is authoritative in normalized PostgreSQL. Draft
# payloads may be updated while exports are added, but the publication seal is
# append-only and can only be read or inserted by the serving role.
REPORT_PUBLICATION_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "assumption_proposals": ("SELECT", "INSERT", "UPDATE"),
    "evidence_graphs": ("SELECT", "INSERT", "UPDATE"),
    "market_evidence": ("SELECT", "INSERT", "UPDATE"),
    "orchestrations": ("SELECT", "INSERT", "UPDATE"),
    "project_snapshots": ("SELECT", "INSERT", "UPDATE"),
    "reports": ("SELECT", "INSERT", "UPDATE"),
    "report_publications": ("SELECT", "INSERT"),
    "risk_registers": ("SELECT", "INSERT", "UPDATE"),
}

# The API consumes the verified public/legal corpus but never mutates it.
# Synchronization and corpus ingestion run under a separately governed worker
# role.  Keeping the synchronization ledger visible lets the API disclose
# coverage/freshness without inheriting ingestion authority.
LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "legal_corpus_required_target_contracts": ("SELECT",),
    "legal_corpus_coverage_registries": ("SELECT",),
    "legal_corpus_coverage_contracts": ("SELECT",),
    "legal_source_providers": ("SELECT",),
    "legal_sync_runs": ("SELECT",),
    "legal_sync_run_events": ("SELECT",),
    "legal_sync_scope_lease_events": ("SELECT",),
    "legal_sync_checkpoint_events": ("SELECT",),
    "legal_corpus_scope_completion_attestations": ("SELECT",),
    "legal_source_fetches": ("SELECT",),
    "legal_resources": ("SELECT",),
    "legal_resource_identity_crosswalks": ("SELECT",),
    "legal_resource_versions": ("SELECT",),
    "legal_resource_version_lineages": ("SELECT",),
    "legal_provisions": ("SELECT",),
    "legal_citations": ("SELECT",),
    "legal_citation_review_events": (),
    "legal_relation_assertions": ("SELECT",),
    "legal_relation_review_events": (),
    "legal_term_definitions": ("SELECT",),
    "legal_term_provision_links": ("SELECT",),
    "legal_term_source_links": ("SELECT",),
    "legal_term_relations": ("SELECT",),
    "legal_semantic_artifacts": ("SELECT",),
}

# Tenant data is an append-only audit/evidence overlay.  RLS supplies tenant
# isolation and the runtime role may only read or append; review history is
# never rewritten or deleted.
LEGAL_INTELLIGENCE_TENANT_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "legal_query_runs": ("SELECT", "INSERT"),
    "legal_query_run_events": ("SELECT", "INSERT"),
    "project_legal_resource_links": ("SELECT", "INSERT"),
    "legal_findings": ("SELECT", "INSERT"),
    "legal_evidence_snapshots": ("SELECT", "INSERT"),
    "legal_evidence_snapshot_items": ("SELECT", "INSERT"),
    "legal_review_decisions": ("SELECT",),
    "legal_approval_delegations": ("SELECT",),
    "legal_required_claim_sets": ("SELECT",),
    "legal_required_claims": ("SELECT",),
    "project_legal_matters": ("SELECT",),
    "legal_waivers": ("SELECT",),
    "counsel_opinions": ("SELECT",),
    "legal_analysis_versions": ("SELECT",),
    "project_legal_annotations": ("SELECT",),
    "tenant_legal_term_aliases": ("SELECT",),
    "tenant_legal_term_alias_review_events": ("SELECT",),
    "project_legal_review_versions": ("SELECT", "INSERT"),
    "project_permit_review_versions": ("SELECT", "INSERT"),
    "project_legal_citation_review_versions": ("SELECT",),
    "project_legal_citation_review_events": ("SELECT",),
    "project_legal_relation_review_versions": ("SELECT",),
    "project_legal_relation_review_events": ("SELECT",),
}

# The canonical glossary remains owned by its governed curation workflow.  The
# legal runtime can resolve term IDs but cannot promote or rewrite definitions.
LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES: Mapping[
    str, tuple[str, ...]
] = {
    "glossary_terms": ("SELECT",),
}

# Narrow, side-effect-free helpers used by authoritative normalized reads.
# Migration 0009 revokes PUBLIC execution, so the serving role receives only
# the exact parser needed to fail closed on malformed waiver deadlines.
LEGAL_INTELLIGENCE_RUNTIME_FUNCTIONS = (
    "legal_try_timestamptz(TEXT)",
)
LEGAL_INTELLIGENCE_RUNTIME_EXTENSION_FUNCTIONS = (
    "digest(bytea, text)",
)

# Provider operational writes and replay reads/appends use the dedicated
# REALYTICS_LEGAL_OPERATIONS_DATABASE_URL principal.  Keeping these relations
# in the serving-runtime manifest with an empty allowlist makes any accidental
# fallback grant auditable and ensures provisioning revokes historical access.
LEGAL_PROVIDER_OPERATIONS_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "legal_provider_quarantine": (),
    "legal_provider_dead_letters": (),
    "legal_provider_metrics": (),
    "legal_provider_replay_objects": (),
    "legal_provider_replay_approval_events": (),
    "legal_provider_replay_requests": (),
    "legal_provider_replay_events": (),
}

FORBIDDEN_APPEND_ONLY_PRIVILEGES = (
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
FORBIDDEN_READ_ONLY_PRIVILEGES = (
    "INSERT",
    *FORBIDDEN_APPEND_ONLY_PRIVILEGES,
)
FORBIDDEN_MANAGED_SUPPORT_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "assumption_overrides": FORBIDDEN_READ_ONLY_PRIVILEGES,
    "schema_migrations": (
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ),
    "runtime_outbox_events": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    **{table: ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER") for table in (
        "calculation_runs", "deal_state_records", "chief_pod_artifacts",
        "pod_ai_reviews", "agent_handoffs", "recalculation_events",
        "async_jobs", "agent_runs", "agent_run_steps", "brain_usage_events",
    )},
}
REPORT_PUBLICATION_FORBIDDEN_PRIVILEGES: Mapping[
    str, tuple[str, ...]
] = {
    "assumption_proposals": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "evidence_graphs": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "market_evidence": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "orchestrations": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "project_snapshots": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "reports": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "report_publications": FORBIDDEN_APPEND_ONLY_PRIVILEGES,
    "risk_registers": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
}
PROJECT_RUNTIME_FORBIDDEN_PRIVILEGES: Mapping[
    str, tuple[str, ...]
] = {
    "projects": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "user_activity_logs": FORBIDDEN_APPEND_ONLY_PRIVILEGES,
}
PROJECT_RUNTIME_SEQUENCE_FORBIDDEN_PRIVILEGES: Mapping[
    str, tuple[str, ...]
] = {
    "project_id_sequence": ("SELECT", "UPDATE"),
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


class RuntimePrivilegeProvisioningError(RuntimeError):
    """The requested runtime role cannot be safely provisioned."""


def validate_database_identifier(value: str, *, label: str) -> str:
    identifier = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(identifier):
        raise RuntimePrivilegeProvisioningError(
            f"{label} must be a simple PostgreSQL identifier (1-63 characters)"
        )
    return identifier


def assert_current_schema(cursor: Any, *, schema_name: str) -> str:
    """Fail before DDL/grants when the DSN search path targets another schema."""

    expected = validate_database_identifier(
        schema_name,
        label="database schema",
    )
    cursor.execute("SELECT current_schema()")
    row = cursor.fetchone()
    actual = str(row[0] or "") if row else ""
    if actual != expected:
        raise RuntimePrivilegeProvisioningError(
            "PostgreSQL DSN search_path targets schema "
            f"{actual!r}; expected {expected!r}"
        )
    return actual


def required_table_privileges() -> dict[str, tuple[str, ...]]:
    """Return every positive privilege required by normalized runtime domains."""

    return {
        **PARSER_V2_TABLE_PRIVILEGES,
        **PROJECT_RUNTIME_TABLE_PRIVILEGES,
        **PARSER_V2_SUPPORT_PRIVILEGES,
        **PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
        **MEMBERSHIP_ADMIN_TABLE_PRIVILEGES,
        **REPORT_PUBLICATION_PRIVILEGES,
        **LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES,
        **LEGAL_INTELLIGENCE_TENANT_PRIVILEGES,
        **LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES,
        **LEGAL_PROVIDER_OPERATIONS_PRIVILEGES,
    }


def forbidden_table_privileges() -> dict[str, tuple[str, ...]]:
    """Return effective privileges that the runtime role must not possess."""

    return {
        **{
            table: FORBIDDEN_APPEND_ONLY_PRIVILEGES
            for table in PARSER_V2_TABLE_PRIVILEGES
        },
        **{
            table: FORBIDDEN_READ_ONLY_PRIVILEGES
            for table in LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES
        },
        **{
            table: FORBIDDEN_APPEND_ONLY_PRIVILEGES
            for table in LEGAL_INTELLIGENCE_TENANT_PRIVILEGES
        },
        **{
            table: FORBIDDEN_READ_ONLY_PRIVILEGES
            for table in LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES
        },
        **{
            table: (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            )
            for table in LEGAL_PROVIDER_OPERATIONS_PRIVILEGES
        },
        **dict(FORBIDDEN_MANAGED_SUPPORT_PRIVILEGES),
        **MEMBERSHIP_ADMIN_FORBIDDEN_TABLE_PRIVILEGES,
        **REPORT_PUBLICATION_FORBIDDEN_PRIVILEGES,
        **PROJECT_RUNTIME_FORBIDDEN_PRIVILEGES,
    }


def required_sequence_privileges() -> dict[str, tuple[str, ...]]:
    """Return sequence privileges required by normalized runtime domains."""

    return dict(PROJECT_RUNTIME_SEQUENCE_PRIVILEGES)


def forbidden_sequence_privileges() -> dict[str, tuple[str, ...]]:
    """Return sequence privileges broader than the runtime contract."""

    return dict(PROJECT_RUNTIME_SEQUENCE_FORBIDDEN_PRIVILEGES)


def required_column_privileges(
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Return exact column grants for membership administration."""

    return {
        table: dict(privileges)
        for table, privileges in MEMBERSHIP_ADMIN_COLUMN_PRIVILEGES.items()
    }


def forbidden_column_privileges(
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Return identity columns that the serving runtime must not access."""

    return {
        table: dict(privileges)
        for table, privileges in (
            MEMBERSHIP_ADMIN_FORBIDDEN_COLUMN_PRIVILEGES.items()
        )
    }


def audit_runtime_privileges(
    cursor: Any,
    *,
    schema_name: str,
    postgis_schema_name: str = DEFAULT_POSTGIS_SCHEMA,
    require_no_database_temporary: bool = False,
) -> tuple[list[str], list[str]]:
    """Return missing required and unexpectedly granted effective privileges."""

    schema = validate_database_identifier(schema_name, label="database schema")
    postgis_schema = validate_database_identifier(
        postgis_schema_name,
        label="PostGIS schema",
    )
    missing: list[str] = []
    forbidden: list[str] = []

    if require_no_database_temporary:
        cursor.execute(
            "SELECT has_database_privilege("
            "current_user, current_database(), 'TEMPORARY'"
            ")"
        )
        database_temporary = cursor.fetchone()
        if database_temporary and bool(database_temporary[0]):
            forbidden.append("database.TEMPORARY")

    for scoped_schema in dict.fromkeys((schema, postgis_schema)):
        cursor.execute(
            "SELECT has_schema_privilege(current_user, %s, 'USAGE')",
            (scoped_schema,),
        )
        schema_usage = cursor.fetchone()
        if not schema_usage or not bool(schema_usage[0]):
            missing.append(f"schema:{scoped_schema}.USAGE")

        cursor.execute(
            "SELECT has_schema_privilege(current_user, %s, 'CREATE')",
            (scoped_schema,),
        )
        schema_create = cursor.fetchone()
        if schema_create and bool(schema_create[0]):
            forbidden.append(f"schema:{scoped_schema}.CREATE")

    for table, privileges in required_table_privileges().items():
        for privilege in privileges:
            if not _has_table_privilege(
                cursor,
                schema=schema,
                table=table,
                privilege=privilege,
            ):
                missing.append(f"{table}.{privilege}")

    for table, privileges in forbidden_table_privileges().items():
        for privilege in privileges:
            if _has_table_privilege(
                cursor,
                schema=schema,
                table=table,
                privilege=privilege,
            ):
                forbidden.append(f"{table}.{privilege}")

    for table, grants in required_column_privileges().items():
        for privilege, columns in grants.items():
            for column in columns:
                if not _has_column_privilege(
                    cursor,
                    schema=schema,
                    table=table,
                    column=column,
                    privilege=privilege,
                ):
                    missing.append(f"{table}.{column}.{privilege}")

    for table, grants in forbidden_column_privileges().items():
        for privilege, columns in grants.items():
            for column in columns:
                if _has_column_privilege(
                    cursor,
                    schema=schema,
                    table=table,
                    column=column,
                    privilege=privilege,
                ):
                    forbidden.append(f"{table}.{column}.{privilege}")

    for sequence, privileges in required_sequence_privileges().items():
        for privilege in privileges:
            if not _has_sequence_privilege(
                cursor,
                schema=schema,
                sequence=sequence,
                privilege=privilege,
            ):
                missing.append(f"{sequence}.{privilege}")

    for sequence, privileges in forbidden_sequence_privileges().items():
        for privilege in privileges:
            if _has_sequence_privilege(
                cursor,
                schema=schema,
                sequence=sequence,
                privilege=privilege,
            ):
                forbidden.append(f"{sequence}.{privilege}")

    return sorted(missing), sorted(forbidden)


def provision_parser_v2_runtime_role(
    conn: Any,
    *,
    role_name: str,
    schema_name: str = DEFAULT_RUNTIME_SCHEMA,
    postgis_schema_name: str = DEFAULT_POSTGIS_SCHEMA,
    migration_lock_key: int,
    revoke_public_database_temporary: bool = False,
) -> None:
    """Apply the normalized runtime role manifest inside the transaction.

    The caller owns commit/rollback.  Parser tables, managed support tables,
    project CRUD objects, Legal Intelligence corpus/overlay tables, and its
    glossary dependency are reset to their exact allowlists.  Calculation
    support remains additive SELECT until its complete write contract is
    promoted into this manifest.  The public function name is kept for
    deployment compatibility.
    """

    role = validate_database_identifier(role_name, label="runtime role")
    schema = validate_database_identifier(schema_name, label="database schema")
    postgis_schema = validate_database_identifier(
        postgis_schema_name,
        label="PostGIS schema",
    )
    scoped_tables = tuple(
        dict.fromkeys(
            (
                *PARSER_V2_TABLE_PRIVILEGES,
                *PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
                *PROJECT_RUNTIME_TABLE_PRIVILEGES,
                *MEMBERSHIP_ADMIN_TABLE_PRIVILEGES,
                *PARSER_V2_SUPPORT_PRIVILEGES,
                *REPORT_PUBLICATION_PRIVILEGES,
                *LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES,
                *LEGAL_INTELLIGENCE_TENANT_PRIVILEGES,
                *LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES,
                *LEGAL_PROVIDER_OPERATIONS_PRIVILEGES,
            )
        )
    )

    with conn.cursor() as cursor:
        assert_current_schema(cursor, schema_name=schema)
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (migration_lock_key,))
        cursor.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, "
            "rolbypassrls, rolreplication "
            "FROM pg_roles WHERE rolname = %s",
            (role,),
        )
        state = cursor.fetchone()
        if state is None:
            raise RuntimePrivilegeProvisioningError(
                f"PostgreSQL runtime role does not exist: {role}"
            )
        if (
            bool(state[0])
            or bool(state[1])
            or bool(state[2])
            or bool(state[3])
            or bool(state[4])
            or bool(state[5])
        ):
            raise RuntimePrivilegeProvisioningError(
                "PostgreSQL runtime role must be NOSUPERUSER, NOCREATEDB, "
                "NOCREATEROLE, NOINHERIT, NOBYPASSRLS, and NOREPLICATION "
                "before grants are applied"
            )

        cursor.execute(
            """
            SELECT parent_role.rolname
            FROM pg_auth_members AS membership
            JOIN pg_roles AS member_role
              ON member_role.oid = membership.member
            JOIN pg_roles AS parent_role
              ON parent_role.oid = membership.roleid
            WHERE member_role.rolname = %s
            ORDER BY parent_role.rolname
            """,
            (role,),
        )
        memberships = [str(row[0]) for row in cursor.fetchall()]
        if memberships:
            raise RuntimePrivilegeProvisioningError(
                "PostgreSQL runtime role must not be a member of other roles: "
                + ", ".join(memberships)
            )

        cursor.execute(
            """
            SELECT c.relname
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_roles AS owner_role ON owner_role.oid = c.relowner
            WHERE n.nspname = %s AND owner_role.rolname = %s
              AND c.relname = ANY(%s)
            ORDER BY c.relname
            """,
            (schema, role, list(scoped_tables)),
        )
        owned = [str(row[0]) for row in cursor.fetchall()]
        if owned:
            raise RuntimePrivilegeProvisioningError(
                "runtime role must not own normalized runtime objects: "
                + ", ".join(owned)
            )

        cursor.execute("SELECT current_database()")
        database_row = cursor.fetchone()
        database_name = str(database_row[0] or "") if database_row else ""
        if not database_name:
            raise RuntimePrivilegeProvisioningError(
                "PostgreSQL current database could not be determined"
            )
        for statement in render_parser_v2_runtime_role_sql(
            role_name=role,
            schema_name=schema,
            postgis_schema_name=postgis_schema,
            database_name=database_name,
            revoke_public_database_temporary=(
                revoke_public_database_temporary
            ),
        ):
            cursor.execute(statement)


def render_parser_v2_runtime_role_sql(
    *,
    role_name: str,
    schema_name: str = DEFAULT_RUNTIME_SCHEMA,
    postgis_schema_name: str = DEFAULT_POSTGIS_SCHEMA,
    database_name: str,
    revoke_public_database_temporary: bool = False,
) -> tuple[str, ...]:
    """Render the validated runtime-role manifest as executable SQL.

    The deployment provisioner and an external SQL execution surface (for
    example, Supabase's Management API) can apply this exact manifest after
    performing the same role-attribute and object-ownership preflight checks.
    The database-scoped role setting avoids changing the role in unrelated
    databases.
    """

    role = validate_database_identifier(role_name, label="runtime role")
    schema = validate_database_identifier(schema_name, label="database schema")
    postgis_schema = validate_database_identifier(
        postgis_schema_name,
        label="PostGIS schema",
    )
    database = str(database_name or "")
    if not database:
        raise RuntimePrivilegeProvisioningError(
            "database name must not be empty"
        )

    role_identifier = _quoted_identifier(role)
    schema_identifier = _quoted_identifier(schema)
    postgis_schema_identifier = _quoted_identifier(postgis_schema)
    database_identifier = _quoted_identifier(database)
    statements: list[str] = []
    if revoke_public_database_temporary:
        # PostgreSQL grants database TEMPORARY to PUBLIC by default. A revoke
        # from only the runtime role is therefore ineffective; strict mode must
        # revoke PUBLIC and may affect other roles in a shared Supabase DB.
        statements.extend(
            (
                "REVOKE TEMPORARY ON DATABASE "
                f"{database_identifier} FROM PUBLIC",
                "REVOKE TEMPORARY ON DATABASE "
                f"{database_identifier} FROM {role_identifier}",
            )
        )

    for scoped_schema_identifier in dict.fromkeys(
        (schema_identifier, postgis_schema_identifier)
    ):
        statements.extend(
            (
                f"REVOKE CREATE ON SCHEMA {scoped_schema_identifier} FROM PUBLIC",
                "REVOKE ALL PRIVILEGES ON SCHEMA "
                f"{scoped_schema_identifier} FROM {role_identifier}",
                "GRANT USAGE ON SCHEMA "
                f"{scoped_schema_identifier} TO {role_identifier}",
            )
        )

    # Keep the application schema first so current_schema() remains stable.
    # pg_catalog is searched implicitly before explicit entries; naming
    # pg_temp last prevents PostgreSQL from implicitly searching it first.
    search_path = tuple(dict.fromkeys((schema, postgis_schema, "pg_temp")))
    search_path_sql = ", ".join(_quoted_identifier(item) for item in search_path)
    statements.append(
        f"ALTER ROLE {role_identifier} IN DATABASE {database_identifier} "
        f"SET search_path TO {search_path_sql}"
    )
    statements.append(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA "
        f"{schema_identifier} REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC"
    )

    exact_privileges = {
        **PARSER_V2_TABLE_PRIVILEGES,
        **PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
        **PROJECT_RUNTIME_TABLE_PRIVILEGES,
        **MEMBERSHIP_ADMIN_TABLE_PRIVILEGES,
        **REPORT_PUBLICATION_PRIVILEGES,
        **LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES,
        **LEGAL_INTELLIGENCE_TENANT_PRIVILEGES,
        **LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES,
        **LEGAL_PROVIDER_OPERATIONS_PRIVILEGES,
    }
    for table, privileges in exact_privileges.items():
        relation = f"{schema_identifier}.{_quoted_identifier(table)}"
        statements.extend(
            (
                f"REVOKE ALL PRIVILEGES ON TABLE {relation} FROM PUBLIC",
                "REVOKE ALL PRIVILEGES ON TABLE "
                f"{relation} FROM {role_identifier}",
            )
        )
        if privileges:
            statements.append(
                _render_grant(relation, role_identifier, privileges)
            )

    for table, grants in MEMBERSHIP_ADMIN_COLUMN_PRIVILEGES.items():
        relation = f"{schema_identifier}.{_quoted_identifier(table)}"
        for privilege, columns in grants.items():
            statements.append(
                _render_column_grant(
                    relation,
                    role_identifier,
                    privilege=privilege,
                    columns=columns,
                )
            )

    for table, privileges in PARSER_V2_SUPPORT_PRIVILEGES.items():
        relation = f"{schema_identifier}.{_quoted_identifier(table)}"
        statements.append(_render_grant(relation, role_identifier, privileges))

    statements.append(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA "
        f"{schema_identifier} REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC"
    )
    for sequence, privileges in PROJECT_RUNTIME_SEQUENCE_PRIVILEGES.items():
        relation = f"{schema_identifier}.{_quoted_identifier(sequence)}"
        statements.extend(
            (
                f"REVOKE ALL PRIVILEGES ON SEQUENCE {relation} FROM PUBLIC",
                "REVOKE ALL PRIVILEGES ON SEQUENCE "
                f"{relation} FROM {role_identifier}",
                _render_sequence_grant(
                    relation,
                    role_identifier,
                    privileges,
                ),
            )
        )

    for signature in LEGAL_INTELLIGENCE_RUNTIME_FUNCTIONS:
        statements.append(
            "GRANT EXECUTE ON FUNCTION "
            f"{schema_identifier}.{signature} TO {role_identifier}"
        )
    for signature in LEGAL_INTELLIGENCE_RUNTIME_EXTENSION_FUNCTIONS:
        statements.append(
            "GRANT EXECUTE ON FUNCTION "
            f"{postgis_schema_identifier}.{signature} TO {role_identifier}"
        )

    return tuple(statements)


def revoke_public_database_temporary_enabled() -> bool:
    """Return whether deployment opted into shared-database TEMP hardening."""

    return os.environ.get(
        REVOKE_PUBLIC_DATABASE_TEMPORARY_ENV,
        "",
    ).strip().casefold() in {"1", "true", "yes", "on"}


def _render_grant(
    relation: str,
    role_identifier: str,
    privileges: Sequence[str],
) -> str:
    privilege_sql = ", ".join(privileges)
    return f"GRANT {privilege_sql} ON TABLE {relation} TO {role_identifier}"


def _render_sequence_grant(
    relation: str,
    role_identifier: str,
    privileges: Sequence[str],
) -> str:
    privilege_sql = ", ".join(privileges)
    return (
        f"GRANT {privilege_sql} ON SEQUENCE {relation} "
        f"TO {role_identifier}"
    )


def _render_column_grant(
    relation: str,
    role_identifier: str,
    *,
    privilege: str,
    columns: Sequence[str],
) -> str:
    privilege_name = str(privilege or "").strip().upper()
    if privilege_name not in {"SELECT", "INSERT", "UPDATE", "REFERENCES"}:
        raise RuntimePrivilegeProvisioningError(
            f"unsupported column privilege: {privilege_name}"
        )
    if not columns:
        raise RuntimePrivilegeProvisioningError(
            "column privilege grant requires at least one column"
        )
    column_sql = ", ".join(
        _quoted_identifier(
            validate_database_identifier(column, label="database column")
        )
        for column in columns
    )
    return (
        f"GRANT {privilege_name} ({column_sql}) ON TABLE {relation} "
        f"TO {role_identifier}"
    )


def _quoted_identifier(value: str) -> str:
    """Quote a previously validated PostgreSQL identifier.

    Keeping SQL composition dependency-free lets the privilege manifest be
    unit-tested without the optional psycopg driver.  Runtime connection setup
    still requires psycopg before this provisioner can reach the database.
    """

    return '"' + value.replace('"', '""') + '"'


def _has_table_privilege(
    cursor: Any,
    *,
    schema: str,
    table: str,
    privilege: str,
) -> bool:
    cursor.execute(
        """
        SELECT CASE
            WHEN relation_oid IS NULL THEN FALSE
            ELSE has_table_privilege(current_user, relation_oid, %s)
        END
        FROM (
            SELECT to_regclass(
                format('%%I.%%I', %s::text, %s::text)
            ) AS relation_oid
        ) AS runtime_relation
        """,
        (privilege, schema, table),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _has_column_privilege(
    cursor: Any,
    *,
    schema: str,
    table: str,
    column: str,
    privilege: str,
) -> bool:
    cursor.execute(
        """
        SELECT CASE
            WHEN relation_oid IS NULL THEN FALSE
            ELSE has_column_privilege(
                current_user, relation_oid, %s, %s
            )
        END
        FROM (
            SELECT to_regclass(
                format('%%I.%%I', %s::text, %s::text)
            ) AS relation_oid
        ) AS runtime_relation
        """,
        (column, privilege, schema, table),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _has_sequence_privilege(
    cursor: Any,
    *,
    schema: str,
    sequence: str,
    privilege: str,
) -> bool:
    cursor.execute(
        """
        SELECT CASE
            WHEN relation_oid IS NULL THEN FALSE
            ELSE has_sequence_privilege(current_user, relation_oid, %s)
        END
        FROM (
            SELECT to_regclass(
                format('%%I.%%I', %s::text, %s::text)
            ) AS relation_oid
        ) AS runtime_relation
        """,
        (privilege, schema, sequence),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


__all__ = [
    "DEFAULT_POSTGIS_SCHEMA",
    "DEFAULT_RUNTIME_SCHEMA",
    "FORBIDDEN_APPEND_ONLY_PRIVILEGES",
    "FORBIDDEN_READ_ONLY_PRIVILEGES",
    "LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES",
    "LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES",
    "LEGAL_INTELLIGENCE_RUNTIME_FUNCTIONS",
    "LEGAL_INTELLIGENCE_RUNTIME_EXTENSION_FUNCTIONS",
    "LEGAL_INTELLIGENCE_TENANT_PRIVILEGES",
    "LEGAL_PROVIDER_OPERATIONS_PRIVILEGES",
    "MEMBERSHIP_ADMIN_COLUMN_PRIVILEGES",
    "MEMBERSHIP_ADMIN_FORBIDDEN_COLUMN_PRIVILEGES",
    "MEMBERSHIP_ADMIN_FORBIDDEN_TABLE_PRIVILEGES",
    "MEMBERSHIP_ADMIN_TABLE_PRIVILEGES",
    "PARSER_V2_MANAGED_SUPPORT_PRIVILEGES",
    "PARSER_V2_SUPPORT_PRIVILEGES",
    "PARSER_V2_TABLE_PRIVILEGES",
    "PROJECT_RUNTIME_FORBIDDEN_PRIVILEGES",
    "PROJECT_RUNTIME_SEQUENCE_FORBIDDEN_PRIVILEGES",
    "PROJECT_RUNTIME_SEQUENCE_PRIVILEGES",
    "PROJECT_RUNTIME_TABLE_PRIVILEGES",
    "REPORT_PUBLICATION_FORBIDDEN_PRIVILEGES",
    "REPORT_PUBLICATION_PRIVILEGES",
    "POSTGIS_SCHEMA_ENV",
    "REVOKE_PUBLIC_DATABASE_TEMPORARY_ENV",
    "RUNTIME_ROLE_ENV",
    "RUNTIME_SCHEMA_ENV",
    "RuntimePrivilegeProvisioningError",
    "assert_current_schema",
    "audit_runtime_privileges",
    "forbidden_table_privileges",
    "forbidden_column_privileges",
    "forbidden_sequence_privileges",
    "provision_parser_v2_runtime_role",
    "render_parser_v2_runtime_role_sql",
    "required_table_privileges",
    "required_column_privileges",
    "required_sequence_privileges",
    "revoke_public_database_temporary_enabled",
    "validate_database_identifier",
]
