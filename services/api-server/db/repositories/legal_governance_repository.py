"""Append-only PostgreSQL repository for tenant Legal Intelligence governance."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from db.legal_review_database import connect_legal_review_database
from db.postgres_runtime_privileges import validate_database_identifier
from legal_intelligence.governance_models import (
    CounselOpinionVersionInput,
    LegalAnalysisReviewInput,
    LegalAnalysisVersionInput,
    LegalApprovalDelegationInput,
    LegalApprovalDelegationRevokeInput,
    LegalAnnotationInput,
    LegalMatterVersionInput,
    LegalRequiredClaimSetInput,
    LegalWaiverCureInput,
    LegalWaiverVersionInput,
    TenantLegalTermAliasInput,
    TenantLegalTermAliasReviewInput,
)


ConnectionFactory = Callable[[], Any]
FINAL_COUNSEL_ROLES = frozenset(
    {"legal_counsel_approver", "external_counsel"}
)
TERMINAL_MATTER_STATUSES = frozenset(
    {"resolved", "waived", "closed", "superseded"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return str(value).casefold().removeprefix("sha256:")


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _claim_set_hash(value: LegalRequiredClaimSetInput) -> str:
    canonical = "\n".join(
        (
            f"{claim.claim_key}:"
            f"{claim.claim_text_sha256.casefold().removeprefix('sha256:')}"
        )
        for claim in value.claims
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PostgresLegalGovernanceRepository:
    """Tenant/project-bound INSERT-only governance repository.

    Every transaction installs the tenant GUC before accessing an RLS table.
    The constructor binds one project so a caller cannot swap project scope in
    an individual payload.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_factory: ConnectionFactory = connect_legal_review_database,
        schema_name: str | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.tenant_id = str(tenant_id or "").strip()
        self.project_id = str(project_id or "").strip()
        if not self.tenant_id or not self.project_id:
            raise ValueError("tenant_id and project_id are required")
        self._connect = connection_factory
        self._schema = validate_database_identifier(
            schema_name
            or os.environ.get("REALYTICS_DATABASE_SCHEMA")
            or "public",
            label="database schema",
        )
        self._now = now

    def append_matter(
        self,
        value: LegalMatterVersionInput,
        *,
        actor_id: str,
        actor_role: str,
    ) -> str:
        actor = self._actor(actor_id)
        role = str(actor_role or "").strip().casefold()
        terminal = value.status.value in TERMINAL_MATTER_STATUSES
        if terminal:
            self._final_counsel(actor, role)
        elif value.legal_approval_delegation_id is not None:
            raise ValueError(
                "legalApprovalDelegationId is only valid for a terminal "
                "matter status"
            )
        matter_id = f"legal-matter:{uuid4()}"
        with self._cursor() as cursor:
            scope_lock = (
                f"project-legal-matter:{self.tenant_id}:"
                f"{self.project_id}:{value.matter_key}"
            )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (scope_lock,),
            )
            cursor.execute(
                """
                SELECT
                    matter.project_legal_matter_id,
                    matter.matter_version,
                    matter.opened_at,
                    matter.matter_type,
                    matter.created_by,
                    root.created_by
                FROM project_legal_matters AS matter
                JOIN project_legal_matters AS root
                  ON root.tenant_id = matter.tenant_id
                 AND root.project_id = matter.project_id
                 AND root.matter_key = matter.matter_key
                 AND root.matter_version = 1
                WHERE matter.tenant_id = %s
                  AND matter.project_id = %s
                  AND matter.matter_key = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM project_legal_matters AS successor
                      WHERE successor.tenant_id = matter.tenant_id
                        AND successor.project_id = matter.project_id
                        AND successor.supersedes_matter_id =
                            matter.project_legal_matter_id
                  )
                ORDER BY matter.matter_version DESC,
                         matter.created_at DESC,
                         matter.project_legal_matter_id DESC
                LIMIT 1
                FOR UPDATE OF matter
                """,
                (self.tenant_id, self.project_id, value.matter_key),
            )
            leaf = cursor.fetchone()
            expected_predecessor = str(
                value.supersedes_matter_id or ""
            ).strip()
            if leaf is None:
                if expected_predecessor:
                    raise LookupError(
                        "legal matter predecessor is not the current leaf"
                    )
                if (
                    "matter_version" in value.model_fields_set
                    and value.matter_version != 1
                ):
                    raise ValueError(
                        "matterVersion must match the server-derived "
                        "root version"
                    )
                matter_version = 1
                supersedes_matter_id = None
                opened_at = self._timestamp(value.opened_at)
                if terminal:
                    raise ValueError(
                        "a terminal legal matter must supersede an active "
                        "matter version"
                    )
            else:
                leaf_id = str(leaf[0] or "").strip()
                if not expected_predecessor:
                    raise ValueError(
                        "an existing legal matter requires "
                        "supersedesMatterId"
                    )
                if expected_predecessor != leaf_id:
                    raise LookupError(
                        "legal matter predecessor is not the current leaf"
                    )
                matter_version = int(leaf[1]) + 1
                if (
                    "matter_version" in value.model_fields_set
                    and value.matter_version != matter_version
                ):
                    raise ValueError(
                        "matterVersion must match the server-derived "
                        "leaf version"
                    )
                if str(leaf[3] or "") != value.matter_type.value:
                    raise ValueError(
                        "matterType cannot change within a matter chain"
                    )
                supersedes_matter_id = leaf_id
                opened_at = self._timestamp(leaf[2])

            resolution_at: datetime | None = None
            delegation_id: str | None = None
            persisted_waiver_id: str | None = None
            waiver_source_id = str(value.legal_waiver_id or "").strip()
            if terminal:
                if leaf is None:
                    raise ValueError(
                        "a terminal legal matter requires a predecessor"
                    )
                if actor.casefold() in {
                    str(leaf[4] or "").strip().casefold(),
                    str(leaf[5] or "").strip().casefold(),
                }:
                    raise ValueError(
                        "maker-checker separation forbids a matter creator "
                        "from finally disposing the matter"
                    )
                resolution_at = self._now()
                self._validate_current_evidence_snapshot(
                    cursor,
                    str(value.legal_evidence_snapshot_id or ""),
                )
                delegation_id = self._validate_delegated_authority(
                    cursor,
                    actor_id=actor,
                    actor_role=role,
                    scope="matter_resolution",
                    delegation_id=value.legal_approval_delegation_id,
                    at=resolution_at,
                )
                if value.status.value == "waived":
                    cursor.execute(
                        """
                        SELECT waiver.legal_waiver_id
                        FROM legal_waivers AS waiver
                        WHERE waiver.legal_waiver_id = %s
                          AND waiver.tenant_id = %s
                          AND waiver.project_id = %s
                          AND waiver.project_legal_matter_id = %s
                          AND waiver.legal_evidence_snapshot_id = %s
                          AND waiver.status = 'approved'
                          AND waiver.valid_from <= %s
                          AND waiver.valid_until > %s
                          AND waiver.cure_status IN (
                              'not_started',
                              'in_progress',
                              'evidence_submitted'
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM legal_waivers AS successor
                              WHERE successor.tenant_id = waiver.tenant_id
                                AND successor.project_id = waiver.project_id
                                AND successor.supersedes_waiver_id =
                                    waiver.legal_waiver_id
                          )
                        FOR UPDATE OF waiver
                        """,
                        (
                            waiver_source_id,
                            self.tenant_id,
                            self.project_id,
                            supersedes_matter_id,
                            value.legal_evidence_snapshot_id,
                            resolution_at,
                            resolution_at,
                        ),
                    )
                    waiver = cursor.fetchone()
                    if (
                        waiver is None
                        or str(waiver[0] or "").strip() != waiver_source_id
                    ):
                        raise ValueError(
                            "a waived matter requires the current approved "
                            "same-snapshot waiver for its predecessor"
                        )
                    persisted_waiver_id = f"legal-waiver:{uuid4()}"
                elif waiver_source_id:
                    raise ValueError(
                        "legalWaiverId is only valid for a waived matter"
                    )

            cursor.execute(
                """
                INSERT INTO project_legal_matters (
                    project_legal_matter_id, tenant_id, project_id,
                    matter_key, matter_version, title, matter_type, status,
                    severity, owner_user_id, due_at, opened_at, closed_at,
                    supersedes_matter_id, created_by, created_at,
                    legal_evidence_snapshot_id, resolution_reason,
                    resolved_by, resolver_role,
                    legal_approval_delegation_id, resolved_at,
                    legal_waiver_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, statement_timestamp(), %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb
                )
                RETURNING project_legal_matter_id
                """,
                (
                    matter_id,
                    self.tenant_id,
                    self.project_id,
                    value.matter_key,
                    matter_version,
                    value.title,
                    value.matter_type.value,
                    value.status.value,
                    value.severity.value,
                    value.owner_user_id,
                    value.due_at,
                    opened_at,
                    value.closed_at,
                    supersedes_matter_id,
                    actor,
                    value.legal_evidence_snapshot_id,
                    value.resolution_reason,
                    actor if terminal else None,
                    role if terminal else None,
                    delegation_id,
                    resolution_at,
                    persisted_waiver_id,
                    _json(value.metadata),
                ),
            )
            inserted_id = self._inserted_id(cursor, "legal matter")
            if persisted_waiver_id is not None:
                cursor.execute(
                    """
                    INSERT INTO legal_waivers (
                        legal_waiver_id, tenant_id, project_id,
                        project_legal_matter_id,
                        legal_evidence_snapshot_id,
                        waiver_version, status, reason, conditions_json,
                        valid_from, valid_until, cure_status,
                        cure_evidence_snapshot_id, proposed_by, proposed_at,
                        recorded_at, approved_by, approver_role,
                        legal_approval_delegation_id, approved_at,
                        cure_reviewed_by, cure_reviewer_role,
                        cure_reviewed_at, cure_reason,
                        cure_condition_outcomes_json,
                        cure_approval_delegation_id,
                        supersedes_waiver_id, metadata_json
                    )
                    SELECT
                        %s, source.tenant_id, source.project_id, %s,
                        source.legal_evidence_snapshot_id,
                        source.waiver_version + 1,
                        source.status, source.reason,
                        source.conditions_json, source.valid_from,
                        source.valid_until, source.cure_status,
                        source.cure_evidence_snapshot_id,
                        source.proposed_by, source.proposed_at,
                        statement_timestamp(), source.approved_by,
                        source.approver_role,
                        source.legal_approval_delegation_id,
                        source.approved_at, source.cure_reviewed_by,
                        source.cure_reviewer_role,
                        source.cure_reviewed_at, source.cure_reason,
                        source.cure_condition_outcomes_json,
                        source.cure_approval_delegation_id,
                        source.legal_waiver_id,
                        source.metadata_json || jsonb_build_object(
                            'transitionType', 'matter_binding',
                            'boundMatterId', %s,
                            'boundAt', statement_timestamp()
                        )
                    FROM legal_waivers AS source
                    WHERE source.legal_waiver_id = %s
                      AND source.tenant_id = %s
                      AND source.project_id = %s
                    RETURNING legal_waiver_id
                    """,
                    (
                        persisted_waiver_id,
                        inserted_id,
                        inserted_id,
                        waiver_source_id,
                        self.tenant_id,
                        self.project_id,
                    ),
                )
                self._inserted_id(cursor, "waived matter binding")
            return inserted_id

    def propose_required_claim_set(
        self,
        value: LegalRequiredClaimSetInput,
        *,
        actor_id: str,
    ) -> str:
        """Append an immutable draft required-claim policy and memberships."""

        actor = self._actor(actor_id)
        claim_set_id = f"legal-required-claim-set:{uuid4()}"
        timestamp = self._now()
        claim_set_sha256 = _claim_set_hash(value)
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO legal_required_claim_sets (
                    legal_required_claim_set_id, tenant_id, project_id,
                    claim_set_key, claim_set_version, review_scope, status,
                    claim_set_sha256, created_by, created_at,
                    approved_by, approver_role,
                    legal_approval_delegation_id, approved_at,
                    supersedes_claim_set_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s,
                    NULL, NULL, NULL, NULL, %s, %s::jsonb
                )
                RETURNING legal_required_claim_set_id
                """,
                (
                    claim_set_id,
                    self.tenant_id,
                    self.project_id,
                    value.claim_set_key,
                    value.claim_set_version,
                    value.review_scope,
                    claim_set_sha256,
                    actor,
                    timestamp,
                    value.supersedes_claim_set_id,
                    _json(value.metadata),
                ),
            )
            inserted_id = self._inserted_id(
                cursor,
                "legal required claim set",
            )
            for ordinal, claim in enumerate(value.claims, start=1):
                cursor.execute(
                    """
                    INSERT INTO legal_required_claims (
                        legal_required_claim_id, tenant_id, project_id,
                        legal_required_claim_set_id, claim_key,
                        claim_text_sha256, description, ordinal,
                        metadata_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
                    """,
                    (
                        f"legal-required-claim:{uuid4()}",
                        self.tenant_id,
                        self.project_id,
                        claim_set_id,
                        claim.claim_key,
                        claim.claim_text_sha256.casefold().removeprefix(
                            "sha256:"
                        ),
                        claim.description,
                        ordinal,
                        _json(claim.metadata),
                    ),
                )
            return inserted_id

    def approve_required_claim_set(
        self,
        claim_set_id: str,
        *,
        actor_id: str,
        actor_role: str,
        legal_approval_delegation_id: str | None = None,
    ) -> str:
        """Approve a policy by appending a maker-checker successor version."""

        actor = self._final_counsel(actor_id, actor_role)
        source_id = str(claim_set_id or "").strip()
        if not source_id:
            raise ValueError("claim_set_id is required")
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (
                    (
                        f"required-claim-set:{self.tenant_id}:"
                        f"{self.project_id}:{source_id}"
                    ),
                ),
            )
            cursor.execute(
                """
                SELECT
                    claim_set.claim_set_key,
                    claim_set.claim_set_version,
                    claim_set.review_scope,
                    claim_set.claim_set_sha256,
                    claim_set.created_by,
                    claim_set.created_at,
                    claim_set.metadata_json,
                    ARRAY_AGG(
                        claim.claim_key
                        ORDER BY claim.ordinal, claim.claim_key
                    ),
                    ARRAY_AGG(
                        claim.claim_text_sha256
                        ORDER BY claim.ordinal, claim.claim_key
                    ),
                    ARRAY_AGG(
                        claim.description
                        ORDER BY claim.ordinal, claim.claim_key
                    ),
                    ARRAY_AGG(
                        claim.metadata_json
                        ORDER BY claim.ordinal, claim.claim_key
                    )
                FROM legal_required_claim_sets AS claim_set
                JOIN legal_required_claims AS claim
                  ON claim.legal_required_claim_set_id =
                         claim_set.legal_required_claim_set_id
                 AND claim.tenant_id = claim_set.tenant_id
                 AND claim.project_id = claim_set.project_id
                WHERE claim_set.legal_required_claim_set_id = %s
                  AND claim_set.tenant_id = %s
                  AND claim_set.project_id = %s
                  AND claim_set.status = 'draft'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM legal_required_claim_sets AS successor
                      WHERE successor.tenant_id = claim_set.tenant_id
                        AND successor.project_id = claim_set.project_id
                        AND successor.claim_set_key =
                            claim_set.claim_set_key
                        AND successor.claim_set_version >
                            claim_set.claim_set_version
                  )
                GROUP BY
                    claim_set.claim_set_key,
                    claim_set.claim_set_version,
                    claim_set.review_scope,
                    claim_set.claim_set_sha256,
                    claim_set.created_by,
                    claim_set.created_at,
                    claim_set.metadata_json
                """,
                (source_id, self.tenant_id, self.project_id),
            )
            source = cursor.fetchone()
            if source is None:
                raise LookupError("active required claim set draft not found")
            if str(source[4]) == actor:
                raise ValueError(
                    "maker-checker separation forbids self-approval of "
                    "a required claim policy"
                )
            approved_at = self._now()
            delegation_id = self._validate_delegated_authority(
                cursor,
                actor_id=actor,
                actor_role=actor_role,
                scope="approval_gate",
                delegation_id=legal_approval_delegation_id,
                at=approved_at,
            )
            claims = [
                {
                    "claimKey": str(claim_key),
                    "claimTextSha256": str(claim_hash),
                    "description": description,
                    "metadata": (
                        dict(metadata)
                        if isinstance(metadata, Mapping)
                        else {}
                    ),
                }
                for claim_key, claim_hash, description, metadata in zip(
                    source[7],
                    source[8],
                    source[9],
                    source[10],
                    strict=True,
                )
            ]
            policy = LegalRequiredClaimSetInput(
                claimSetKey=str(source[0]),
                claimSetVersion=int(source[1]) + 1,
                reviewScope=str(source[2]),
                claims=claims,
                supersedesClaimSetId=source_id,
                metadata=(
                    dict(source[6])
                    if isinstance(source[6], Mapping)
                    else {}
                ),
            )
            computed_hash = _claim_set_hash(policy)
            if computed_hash != str(source[3]).casefold():
                raise ValueError(
                    "required claim policy membership hash mismatch"
                )
            approved_id = f"legal-required-claim-set:{uuid4()}"
            metadata = (
                dict(source[6])
                if isinstance(source[6], Mapping)
                else {}
            )
            metadata.pop("legalApprovalDelegationId", None)
            if delegation_id is not None:
                metadata["legalApprovalDelegationId"] = delegation_id
            cursor.execute(
                """
                INSERT INTO legal_required_claim_sets (
                    legal_required_claim_set_id, tenant_id, project_id,
                    claim_set_key, claim_set_version, review_scope, status,
                    claim_set_sha256, created_by, created_at,
                    approved_by, approver_role,
                    legal_approval_delegation_id, approved_at,
                    supersedes_claim_set_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'approved', %s, %s, %s,
                    %s, %s, %s, %s, %s, %s::jsonb
                )
                RETURNING legal_required_claim_set_id
                """,
                (
                    approved_id,
                    self.tenant_id,
                    self.project_id,
                    source[0],
                    int(source[1]) + 1,
                    source[2],
                    computed_hash,
                    source[4],
                    source[5],
                    actor,
                    str(actor_role or "").strip().casefold(),
                    delegation_id,
                    approved_at,
                    source_id,
                    _json(metadata),
                ),
            )
            inserted_id = self._inserted_id(
                cursor,
                "approved legal required claim set",
            )
            for ordinal, claim in enumerate(policy.claims, start=1):
                cursor.execute(
                    """
                    INSERT INTO legal_required_claims (
                        legal_required_claim_id, tenant_id, project_id,
                        legal_required_claim_set_id, claim_key,
                        claim_text_sha256, description, ordinal,
                        metadata_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
                    """,
                    (
                        f"legal-required-claim:{uuid4()}",
                        self.tenant_id,
                        self.project_id,
                        approved_id,
                        claim.claim_key,
                        claim.claim_text_sha256.casefold().removeprefix(
                            "sha256:"
                        ),
                        claim.description,
                        ordinal,
                        _json(claim.metadata),
                    ),
                )
            return inserted_id

    def grant_approval_delegation(
        self,
        value: LegalApprovalDelegationInput,
        *,
        actor_id: str,
        actor_role: str,
    ) -> str:
        actor = self._actor(actor_id)
        role = str(actor_role or "").strip().casefold()
        if role != "legal_counsel_approver":
            raise PermissionError(
                "only a legal_counsel_approver may grant approval authority"
            )
        if actor == value.grantee_id:
            raise ValueError("a legal approver cannot delegate to themselves")
        valid_from = self._timestamp(value.valid_from)
        if value.valid_until.tzinfo is None:
            raise ValueError("validUntil must include a timezone")
        if value.valid_until <= valid_from:
            raise ValueError("validUntil must be later than validFrom")
        delegation_key = f"legal-approval-delegation:{uuid4()}"
        delegation_id = f"{delegation_key}:v1"
        with self._cursor() as cursor:
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
                    %s, %s, %s, %s, 1, %s, %s, %s, %s, %s,
                    %s, %s, 'active', %s, NULL, %s, %s::jsonb
                )
                RETURNING legal_approval_delegation_id
                """,
                (
                    delegation_id,
                    self.tenant_id,
                    self.project_id,
                    delegation_key,
                    actor,
                    role,
                    value.grantee_id,
                    value.grantee_role,
                    value.scope,
                    valid_from,
                    value.valid_until,
                    value.reason,
                    self._now(),
                    _json(value.metadata),
                ),
            )
            return self._inserted_id(
                cursor,
                "legal approval delegation",
            )

    def revoke_approval_delegation(
        self,
        delegation_id: str,
        value: LegalApprovalDelegationRevokeInput,
        *,
        actor_id: str,
        actor_role: str,
    ) -> str:
        actor = self._actor(actor_id)
        role = str(actor_role or "").strip().casefold()
        if role != "legal_counsel_approver":
            raise PermissionError(
                "only a legal_counsel_approver may revoke approval authority"
            )
        source_id = str(delegation_id or "").strip()
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    delegation_key, delegation_version,
                    grantor_id, grantor_role, grantee_id, grantee_role,
                    scope, valid_from, valid_until, metadata_json
                FROM legal_approval_delegations AS delegation
                WHERE legal_approval_delegation_id = %s
                  AND tenant_id = %s
                  AND project_id = %s
                  AND status = 'active'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM legal_approval_delegations AS successor
                      WHERE successor.tenant_id = delegation.tenant_id
                        AND successor.project_id = delegation.project_id
                        AND successor.delegation_key =
                            delegation.delegation_key
                        AND successor.delegation_version >
                            delegation.delegation_version
                  )
                """,
                (source_id, self.tenant_id, self.project_id),
            )
            source = cursor.fetchone()
            if source is None:
                raise LookupError("active legal approval delegation not found")
            if str(source[2]) != actor:
                raise PermissionError(
                    "only the delegation grantor may revoke this authority"
                )
            revoked_id = (
                f"{source[0]}:v{int(source[1]) + 1}"
            )
            metadata = (
                dict(source[9]) if isinstance(source[9], Mapping) else {}
            )
            metadata.update(value.metadata)
            metadata["revocationReason"] = value.reason
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
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, 'revoked', %s, %s, %s, %s::jsonb
                )
                RETURNING legal_approval_delegation_id
                """,
                (
                    revoked_id,
                    self.tenant_id,
                    self.project_id,
                    source[0],
                    int(source[1]) + 1,
                    source[2],
                    source[3],
                    source[4],
                    source[5],
                    source[6],
                    source[7],
                    source[8],
                    value.reason,
                    source_id,
                    self._now(),
                    _json(metadata),
                ),
            )
            return self._inserted_id(
                cursor,
                "revoked legal approval delegation",
            )

    def propose_waiver(
        self,
        value: LegalWaiverVersionInput,
        *,
        actor_id: str,
    ) -> str:
        if value.status != "proposed":
            raise ValueError(
                "a waiver proposal endpoint may only append proposed status"
            )
        if (
            value.waiver_version != 1
            or value.supersedes_waiver_id is not None
        ):
            raise ValueError(
                "a waiver proposal must start at version 1 without a "
                "predecessor"
            )
        if (
            value.cure_status != "not_started"
            or value.cure_evidence_snapshot_id is not None
        ):
            raise ValueError(
                "a waiver proposal must start with cureStatus=not_started "
                "and no cure evidence snapshot"
            )
        actor = self._actor(actor_id)
        waiver_id = f"legal-waiver:{uuid4()}"
        proposed_at = self._now()
        if value.valid_until <= proposed_at:
            raise ValueError("validUntil must be in the future")
        if any(
            condition.due_at is not None
            and condition.due_at <= proposed_at
            for condition in value.conditions
        ):
            raise ValueError(
                "waiver condition dueAt must be in the future"
            )
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO legal_waivers (
                    legal_waiver_id, tenant_id, project_id,
                    project_legal_matter_id, legal_evidence_snapshot_id,
                    waiver_version, status, reason, conditions_json,
                    valid_from, valid_until, cure_status,
                    cure_evidence_snapshot_id, proposed_by, proposed_at,
                    approved_by, approver_role,
                    legal_approval_delegation_id, approved_at,
                    cure_reviewed_by, cure_reviewer_role, cure_reviewed_at,
                    cure_reason, cure_condition_outcomes_json,
                    cure_approval_delegation_id,
                    supersedes_waiver_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'proposed', %s, %s::jsonb,
                    NULL, %s, 'not_started', NULL, %s, %s,
                    NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL, '[]'::jsonb, NULL,
                    %s, %s::jsonb
                )
                RETURNING legal_waiver_id
                """,
                (
                    waiver_id,
                    self.tenant_id,
                    self.project_id,
                    value.project_legal_matter_id,
                    value.legal_evidence_snapshot_id,
                    value.waiver_version,
                    value.reason,
                    _json(
                        [
                            condition.model_dump(
                                mode="json",
                                by_alias=True,
                            )
                            for condition in value.conditions
                        ]
                    ),
                    value.valid_until,
                    actor,
                    proposed_at,
                    value.supersedes_waiver_id,
                    _json(value.metadata),
                ),
            )
            return self._inserted_id(cursor, "legal waiver proposal")

    def approve_waiver(
        self,
        waiver_id: str,
        *,
        actor_id: str,
        actor_role: str,
        valid_from: datetime | None = None,
        legal_approval_delegation_id: str | None = None,
    ) -> str:
        actor = self._final_counsel(actor_id, actor_role)
        source_id = str(waiver_id or "").strip()
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    project_legal_matter_id,
                    legal_evidence_snapshot_id,
                    waiver_version,
                    reason,
                    conditions_json,
                    valid_until,
                    cure_status,
                    cure_evidence_snapshot_id,
                    proposed_by,
                    proposed_at,
                    metadata_json
                FROM legal_waivers AS source
                WHERE source.legal_waiver_id = %s
                  AND source.tenant_id = %s
                  AND source.project_id = %s
                  AND source.status = 'proposed'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM legal_waivers AS successor
                      WHERE successor.tenant_id = source.tenant_id
                        AND successor.project_id = source.project_id
                        AND successor.supersedes_waiver_id =
                            source.legal_waiver_id
                  )
                FOR UPDATE
                """,
                (source_id, self.tenant_id, self.project_id),
            )
            source = cursor.fetchone()
            if source is None:
                raise LookupError("active legal waiver proposal not found")
            if str(source[8]) == actor:
                raise ValueError(
                    "maker-checker separation forbids self-approval of a waiver"
                )
            if str(source[6]) != "not_started" or source[7] is not None:
                raise ValueError(
                    "a waiver proposal must have no pre-approved cure state"
                )
            approved_id = f"legal-waiver:{uuid4()}"
            approved_at = self._now()
            effective_from = (
                self._timestamp(valid_from)
                if valid_from is not None
                else approved_at
            )
            if source[5] <= effective_from:
                raise ValueError("an expired waiver proposal cannot be approved")
            conditions = (
                list(source[4])
                if isinstance(source[4], list)
                else []
            )
            if not conditions:
                raise ValueError("waiver conditions cannot be empty")
            condition_codes = {
                str(condition.get("code") or "").strip()
                for condition in conditions
                if isinstance(condition, Mapping)
            }
            if "" in condition_codes or len(condition_codes) != len(
                conditions
            ):
                raise ValueError(
                    "waiver conditions must use unique non-empty codes"
                )
            if any(
                self._condition_due_at(condition) <= effective_from
                for condition in conditions
                if self._condition_due_at(condition) is not None
            ):
                raise ValueError(
                    "a waiver with an expired cure condition cannot be approved"
                )
            delegation_id = self._validate_delegated_authority(
                cursor,
                actor_id=actor,
                actor_role=actor_role,
                scope="waiver_approval",
                delegation_id=legal_approval_delegation_id,
                at=approved_at,
            )
            metadata = (
                dict(source[10]) if isinstance(source[10], Mapping) else {}
            )
            metadata.pop("legalApprovalDelegationId", None)
            if delegation_id is not None:
                metadata["legalApprovalDelegationId"] = delegation_id
            cursor.execute(
                """
                INSERT INTO legal_waivers (
                    legal_waiver_id, tenant_id, project_id,
                    project_legal_matter_id, legal_evidence_snapshot_id,
                    waiver_version, status, reason, conditions_json,
                    valid_from, valid_until, cure_status,
                    cure_evidence_snapshot_id, proposed_by, proposed_at,
                    approved_by, approver_role,
                    legal_approval_delegation_id, approved_at,
                    cure_reviewed_by, cure_reviewer_role, cure_reviewed_at,
                    cure_reason, cure_condition_outcomes_json,
                    cure_approval_delegation_id,
                    supersedes_waiver_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'approved', %s, %s::jsonb,
                    %s, %s, 'not_started', NULL, %s, %s,
                    %s, %s, %s, %s,
                    NULL, NULL, NULL, NULL, '[]'::jsonb, NULL,
                    %s, %s::jsonb
                )
                RETURNING legal_waiver_id
                """,
                (
                    approved_id,
                    self.tenant_id,
                    self.project_id,
                    source[0],
                    source[1],
                    int(source[2]) + 1,
                    source[3],
                    _json(source[4]),
                    effective_from,
                    source[5],
                    source[8],
                    source[9],
                    actor,
                    str(actor_role or "").strip().casefold(),
                    delegation_id,
                    approved_at,
                    source_id,
                    _json(metadata),
                ),
            )
            return self._inserted_id(cursor, "approved legal waiver")

    def advance_waiver_cure(
        self,
        waiver_id: str,
        value: LegalWaiverCureInput,
        *,
        actor_id: str,
        actor_role: str,
    ) -> str:
        """Append one valid cure transition to an active waiver chain."""

        actor = self._actor(actor_id)
        role = str(actor_role or "").strip().casefold()
        allowed_roles = {
            "legal_reviewer",
            "legal_counsel_approver",
            "external_counsel",
        }
        if role not in allowed_roles:
            raise PermissionError(
                "waiver cure updates require a legal reviewer or counsel"
            )
        terminal = value.cure_status in {"cured", "failed"}
        if terminal and role not in FINAL_COUNSEL_ROLES:
            raise PermissionError(
                "terminal waiver cure decisions require final counsel approval"
            )

        source_id = str(waiver_id or "").strip()
        if not source_id:
            raise ValueError("waiver_id is required")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    project_legal_matter_id,
                    legal_evidence_snapshot_id,
                    waiver_version,
                    reason,
                    conditions_json,
                    valid_from,
                    valid_until,
                    cure_status,
                    cure_evidence_snapshot_id,
                    proposed_by,
                    proposed_at,
                    approved_by,
                    approver_role,
                    legal_approval_delegation_id,
                    approved_at,
                    cure_reviewed_by,
                    cure_reviewer_role,
                    cure_reviewed_at,
                    cure_reason,
                    cure_condition_outcomes_json,
                    cure_approval_delegation_id,
                    metadata_json
                FROM legal_waivers AS source
                WHERE source.legal_waiver_id = %s
                  AND source.tenant_id = %s
                  AND source.project_id = %s
                  AND source.status = 'approved'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM legal_waivers AS successor
                      WHERE successor.tenant_id = source.tenant_id
                        AND successor.project_id = source.project_id
                        AND successor.supersedes_waiver_id =
                            source.legal_waiver_id
                  )
                FOR UPDATE
                """,
                (source_id, self.tenant_id, self.project_id),
            )
            source = cursor.fetchone()
            if source is None:
                raise LookupError("active approved legal waiver not found")

            timestamp = self._now()
            if source[5] is None or source[6] <= timestamp:
                raise ValueError("an expired or unapproved waiver cannot be cured")
            conditions = (
                list(source[4])
                if isinstance(source[4], list)
                else []
            )
            if not conditions:
                raise ValueError("waiver conditions cannot be empty")
            condition_codes = {
                str(condition.get("code") or "").strip()
                for condition in conditions
                if isinstance(condition, Mapping)
            }
            if "" in condition_codes or len(condition_codes) != len(
                conditions
            ):
                raise ValueError(
                    "waiver conditions must use unique non-empty codes"
                )
            outcome_codes = {
                outcome.code for outcome in value.condition_outcomes
            }
            unknown_outcomes = outcome_codes - condition_codes
            if unknown_outcomes:
                raise ValueError(
                    "conditionOutcomes reference unknown waiver conditions"
                )
            if terminal and outcome_codes != condition_codes:
                raise ValueError(
                    "terminal conditionOutcomes must cover every "
                    "waiver condition"
                )

            current_cure_status = str(source[7])
            expected_current = {
                "in_progress": "not_started",
                "evidence_submitted": "in_progress",
                "cured": "evidence_submitted",
                "failed": "evidence_submitted",
            }[value.cure_status]
            if current_cure_status != expected_current:
                raise ValueError(
                    "invalid waiver cure transition: "
                    f"{current_cure_status} -> {value.cure_status}"
                )

            metadata = (
                dict(source[21]) if isinstance(source[21], Mapping) else {}
            )
            previous_actor = str(source[15] or "").strip()
            if terminal and previous_actor == actor:
                raise ValueError(
                    "maker-checker separation forbids self-verification "
                    "of a waiver cure"
                )

            delegation_id: str | None = None
            if terminal or role == "external_counsel":
                delegation_id = self._validate_delegated_authority(
                    cursor,
                    actor_id=actor,
                    actor_role=role,
                    scope="waiver_approval",
                    delegation_id=value.legal_approval_delegation_id,
                    at=timestamp,
                )
            elif value.legal_approval_delegation_id is not None:
                raise ValueError(
                    "legalApprovalDelegationId is only valid for "
                    "external_counsel"
                )

            evidence_snapshot_id = (
                value.cure_evidence_snapshot_id
                or source[8]
            )
            if (
                terminal
                and str(source[8] or "").strip()
                != str(evidence_snapshot_id or "").strip()
            ):
                raise ValueError(
                    "terminal cure review must use the evidence snapshot "
                    "submitted by the immediate predecessor"
                )
            if evidence_snapshot_id is not None:
                self._validate_current_evidence_snapshot(
                    cursor,
                    evidence_snapshot_id,
                )
            evidence_item_ids = sorted(
                {
                    identifier
                    for outcome in value.condition_outcomes
                    for identifier in outcome.evidence_item_ids
                }
            )
            if evidence_item_ids:
                self._validate_cure_evidence_items(
                    cursor,
                    snapshot_id=str(evidence_snapshot_id or ""),
                    evidence_item_ids=evidence_item_ids,
                )

            target_status = {
                "in_progress": "approved",
                "evidence_submitted": "approved",
                "cured": "cured",
                "failed": "revoked",
            }[value.cure_status]
            target_cure_status = {
                "in_progress": "in_progress",
                "evidence_submitted": "evidence_submitted",
                "cured": "verified",
                "failed": "failed",
            }[value.cure_status]
            transition: dict[str, Any] = {
                "actorId": actor,
                "actorRole": role,
                "at": timestamp.isoformat(),
                "from": current_cure_status,
                "to": value.cure_status,
                "reason": value.reason,
            }
            if delegation_id is not None:
                transition["legalApprovalDelegationId"] = delegation_id
            condition_outcomes = [
                outcome.model_dump(mode="json", by_alias=True)
                for outcome in value.condition_outcomes
            ]
            transition_metadata = dict(value.metadata)
            transition_metadata.pop("legalApprovalDelegationId", None)
            metadata.update(transition_metadata)
            metadata["cureTransition"] = transition
            metadata["cureConditionOutcomes"] = condition_outcomes

            successor_id = f"legal-waiver:{uuid4()}"
            cursor.execute(
                """
                INSERT INTO legal_waivers (
                    legal_waiver_id, tenant_id, project_id,
                    project_legal_matter_id, legal_evidence_snapshot_id,
                    waiver_version, status, reason, conditions_json,
                    valid_from, valid_until, cure_status,
                    cure_evidence_snapshot_id, proposed_by, proposed_at,
                    approved_by, approver_role,
                    legal_approval_delegation_id, approved_at,
                    cure_reviewed_by, cure_reviewer_role, cure_reviewed_at,
                    cure_reason, cure_condition_outcomes_json,
                    cure_approval_delegation_id,
                    supersedes_waiver_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s::jsonb, %s,
                    %s, %s::jsonb
                )
                RETURNING legal_waiver_id
                """,
                (
                    successor_id,
                    self.tenant_id,
                    self.project_id,
                    source[0],
                    source[1],
                    int(source[2]) + 1,
                    target_status,
                    source[3],
                    _json(conditions),
                    source[5],
                    source[6],
                    target_cure_status,
                    evidence_snapshot_id,
                    source[9],
                    source[10],
                    source[11],
                    source[12],
                    source[13],
                    source[14],
                    actor,
                    role,
                    timestamp,
                    value.reason,
                    _json(condition_outcomes),
                    delegation_id,
                    source_id,
                    _json(metadata),
                ),
            )
            return self._inserted_id(cursor, "legal waiver cure transition")

    def append_counsel_opinion(
        self,
        value: CounselOpinionVersionInput,
        *,
        actor_id: str,
        actor_role: str,
    ) -> str:
        actor = self._final_counsel(actor_id, actor_role)
        if value.status not in {"draft", "issued"}:
            raise ValueError(
                "new counsel opinion content must be draft or issued; "
                "use a dedicated transition workflow for reviewed states"
            )
        opinion_id = f"counsel-opinion:{uuid4()}"
        authored_at = self._timestamp(value.authored_at)
        with self._cursor() as cursor:
            opinion_version = 1
            predecessor_id = str(
                value.supersedes_opinion_id or ""
            ).strip() or None
            if predecessor_id is not None:
                cursor.execute(
                    """
                    SELECT
                        source.opinion_version,
                        source.authored_at
                    FROM counsel_opinions AS source
                    WHERE source.counsel_opinion_id = %s
                      AND source.tenant_id = %s
                      AND source.project_id = %s
                      AND source.project_legal_matter_id = %s
                      AND source.status IN ('draft', 'issued', 'approved')
                      AND NOT EXISTS (
                          SELECT 1
                          FROM counsel_opinions AS successor
                          WHERE successor.tenant_id = source.tenant_id
                            AND successor.project_id = source.project_id
                            AND successor.supersedes_opinion_id =
                                source.counsel_opinion_id
                      )
                    FOR UPDATE
                    """,
                    (
                        predecessor_id,
                        self.tenant_id,
                        self.project_id,
                        value.project_legal_matter_id,
                    ),
                )
                predecessor = cursor.fetchone()
                if predecessor is None:
                    raise LookupError(
                        "active predecessor counsel opinion not found"
                    )
                opinion_version = int(predecessor[0]) + 1
                if authored_at < predecessor[1]:
                    raise ValueError(
                        "counsel opinion revision predates its predecessor"
                    )
            if value.opinion_version != opinion_version:
                raise ValueError(
                    "opinionVersion must match the server-derived immediate "
                    f"successor version {opinion_version}"
                )
            cursor.execute(
                """
                INSERT INTO counsel_opinions (
                    counsel_opinion_id, tenant_id, project_id,
                    project_legal_matter_id, legal_evidence_snapshot_id,
                    opinion_version, status, object_locator, content_sha256,
                    privileged, authored_by, authored_at, approved_by,
                    approver_role, legal_approval_delegation_id,
                    approved_at, supersedes_opinion_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    NULL, NULL, NULL, NULL, %s, %s::jsonb
                )
                RETURNING counsel_opinion_id
                """,
                (
                    opinion_id,
                    self.tenant_id,
                    self.project_id,
                    value.project_legal_matter_id,
                    value.legal_evidence_snapshot_id,
                    opinion_version,
                    value.status,
                    value.object_locator,
                    _sha256(value.content_sha256),
                    value.privileged,
                    actor,
                    authored_at,
                    predecessor_id,
                    _json(value.metadata),
                ),
            )
            return self._inserted_id(cursor, "counsel opinion")

    def approve_counsel_opinion(
        self,
        opinion_id: str,
        *,
        actor_id: str,
        actor_role: str,
        legal_approval_delegation_id: str | None = None,
    ) -> str:
        actor = self._final_counsel(actor_id, actor_role)
        source_id = str(opinion_id or "").strip()
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    project_legal_matter_id,
                    legal_evidence_snapshot_id,
                    opinion_version,
                    object_locator,
                    content_sha256,
                    privileged,
                    authored_by,
                    authored_at,
                    metadata_json
                FROM counsel_opinions AS source
                WHERE source.counsel_opinion_id = %s
                  AND source.tenant_id = %s
                  AND source.project_id = %s
                  AND source.status IN ('draft', 'issued')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM counsel_opinions AS successor
                      WHERE successor.tenant_id = source.tenant_id
                        AND successor.project_id = source.project_id
                        AND successor.supersedes_opinion_id =
                            source.counsel_opinion_id
                  )
                FOR UPDATE
                """,
                (source_id, self.tenant_id, self.project_id),
            )
            source = cursor.fetchone()
            if source is None:
                raise LookupError("active counsel opinion not found")
            if str(source[6]) == actor:
                raise ValueError(
                    "maker-checker separation forbids self-approval of an opinion"
                )
            approved_id = f"counsel-opinion:{uuid4()}"
            timestamp = self._now()
            delegation_id = self._validate_delegated_authority(
                cursor,
                actor_id=actor,
                actor_role=actor_role,
                scope="counsel_opinion",
                delegation_id=legal_approval_delegation_id,
                at=timestamp,
            )
            metadata = (
                dict(source[8]) if isinstance(source[8], Mapping) else {}
            )
            metadata.pop("legalApprovalDelegationId", None)
            if delegation_id is not None:
                metadata["legalApprovalDelegationId"] = delegation_id
            cursor.execute(
                """
                INSERT INTO counsel_opinions (
                    counsel_opinion_id, tenant_id, project_id,
                    project_legal_matter_id, legal_evidence_snapshot_id,
                    opinion_version, status, object_locator, content_sha256,
                    privileged, authored_by, authored_at, approved_by,
                    approver_role, legal_approval_delegation_id,
                    approved_at, supersedes_opinion_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'approved', %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                RETURNING counsel_opinion_id
                """,
                (
                    approved_id,
                    self.tenant_id,
                    self.project_id,
                    source[0],
                    source[1],
                    int(source[2]) + 1,
                    source[3],
                    source[4],
                    source[5],
                    source[6],
                    source[7],
                    actor,
                    str(actor_role or "").strip().casefold(),
                    delegation_id,
                    timestamp,
                    source_id,
                    _json(metadata),
                ),
            )
            return self._inserted_id(cursor, "approved counsel opinion")

    def append_analysis(
        self,
        value: LegalAnalysisVersionInput,
        *,
        actor_id: str,
    ) -> str:
        actor = self._actor(actor_id)
        analysis_id = f"legal-analysis:{uuid4()}"
        if value.human_review_status != "pending":
            raise ValueError(
                "new analysis must enter the independent review queue as pending"
            )
        with self._cursor() as cursor:
            analysis_version = 1
            predecessor_id = str(
                value.supersedes_analysis_version_id or ""
            ).strip() or None
            if predecessor_id is not None:
                cursor.execute(
                    """
                    SELECT
                        source.analysis_version,
                        source.created_at
                    FROM legal_analysis_versions AS source
                    WHERE source.legal_analysis_version_id = %s
                      AND source.tenant_id = %s
                      AND source.project_id = %s
                      AND source.project_legal_matter_id = %s
                      AND source.analysis_kind = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM legal_analysis_versions AS successor
                          WHERE successor.tenant_id = source.tenant_id
                            AND successor.project_id = source.project_id
                            AND successor.supersedes_analysis_version_id =
                                source.legal_analysis_version_id
                      )
                    FOR UPDATE
                    """,
                    (
                        predecessor_id,
                        self.tenant_id,
                        self.project_id,
                        value.project_legal_matter_id,
                        value.analysis_kind,
                    ),
                )
                predecessor = cursor.fetchone()
                if predecessor is None:
                    raise LookupError(
                        "active predecessor legal analysis not found"
                    )
                analysis_version = int(predecessor[0]) + 1
            if value.analysis_version != analysis_version:
                raise ValueError(
                    "analysisVersion must match the server-derived immediate "
                    f"successor version {analysis_version}"
                )
            cursor.execute(
                """
                INSERT INTO legal_analysis_versions (
                    legal_analysis_version_id, tenant_id, project_id,
                    project_legal_matter_id, legal_evidence_snapshot_id,
                    analysis_kind, analysis_version, object_locator,
                    content_sha256, input_snapshot_sha256, model_id,
                    prompt_sha256, analysis_only, report_eligible,
                    human_review_status, reviewed_by, reviewer_role,
                    legal_approval_delegation_id, reviewed_at,
                    created_by, created_at, supersedes_analysis_version_id,
                    metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, 'pending', NULL, NULL, NULL, NULL,
                    %s, %s, %s, %s::jsonb
                )
                RETURNING legal_analysis_version_id
                """,
                (
                    analysis_id,
                    self.tenant_id,
                    self.project_id,
                    value.project_legal_matter_id,
                    value.legal_evidence_snapshot_id,
                    value.analysis_kind,
                    analysis_version,
                    value.object_locator,
                    _sha256(value.content_sha256),
                    _sha256(value.input_snapshot_sha256),
                    value.model_id,
                    (
                        _sha256(value.prompt_sha256)
                        if value.prompt_sha256
                        else None
                    ),
                    value.analysis_only,
                    value.report_eligible,
                    actor,
                    self._now(),
                    predecessor_id,
                    _json(value.metadata),
                ),
            )
            return self._inserted_id(cursor, "legal analysis")

    def review_analysis(
        self,
        analysis_version_id: str,
        value: LegalAnalysisReviewInput,
        *,
        actor_id: str,
        actor_role: str,
    ) -> str:
        actor = self._actor(actor_id)
        role = str(actor_role or "").strip().casefold()
        allowed_roles = {
            "legal_reviewer",
            "legal_counsel_approver",
            "external_counsel",
        }
        if role not in allowed_roles:
            raise PermissionError(
                "legal analysis review requires a legal reviewer or counsel"
            )
        if value.report_eligible and role not in FINAL_COUNSEL_ROLES:
            raise PermissionError(
                "report-eligible legal analysis requires final counsel approval"
            )

        source_id = str(analysis_version_id or "").strip()
        if not source_id:
            raise ValueError("analysis_version_id is required")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    project_legal_matter_id,
                    legal_evidence_snapshot_id,
                    analysis_kind,
                    analysis_version,
                    object_locator,
                    content_sha256,
                    input_snapshot_sha256,
                    model_id,
                    prompt_sha256,
                    created_by,
                    created_at,
                    metadata_json
                FROM legal_analysis_versions AS source
                WHERE source.legal_analysis_version_id = %s
                  AND source.tenant_id = %s
                  AND source.project_id = %s
                  AND source.human_review_status = 'pending'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM legal_analysis_versions AS successor
                      WHERE successor.tenant_id = source.tenant_id
                        AND successor.project_id = source.project_id
                        AND successor.supersedes_analysis_version_id =
                            source.legal_analysis_version_id
                  )
                FOR UPDATE
                """,
                (source_id, self.tenant_id, self.project_id),
            )
            source = cursor.fetchone()
            if source is None:
                raise LookupError("active pending legal analysis not found")
            if str(source[9]) == actor:
                raise ValueError(
                    "maker-checker separation forbids self-review of an analysis"
                )

            reviewed_id = f"legal-analysis:{uuid4()}"
            reviewed_at = self._now()
            delegation_id: str | None = None
            if role == "external_counsel":
                delegation_id = self._validate_delegated_authority(
                    cursor,
                    actor_id=actor,
                    actor_role=role,
                    scope="approval_gate",
                    delegation_id=value.legal_approval_delegation_id,
                    at=reviewed_at,
                )
            elif value.legal_approval_delegation_id is not None:
                raise ValueError(
                    "legalApprovalDelegationId is only valid for "
                    "external_counsel review"
                )
            metadata = (
                dict(source[11])
                if isinstance(source[11], Mapping)
                else {}
            )
            metadata.pop("legalApprovalDelegationId", None)
            metadata["reviewReason"] = value.reason
            if delegation_id is not None:
                metadata["legalApprovalDelegationId"] = delegation_id
            cursor.execute(
                """
                INSERT INTO legal_analysis_versions (
                    legal_analysis_version_id, tenant_id, project_id,
                    project_legal_matter_id, legal_evidence_snapshot_id,
                    analysis_kind, analysis_version, object_locator,
                    content_sha256, input_snapshot_sha256, model_id,
                    prompt_sha256, analysis_only, report_eligible,
                    human_review_status, reviewed_by, reviewer_role,
                    legal_approval_delegation_id, reviewed_at,
                    created_by, created_at,
                    supersedes_analysis_version_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                RETURNING legal_analysis_version_id
                """,
                (
                    reviewed_id,
                    self.tenant_id,
                    self.project_id,
                    source[0],
                    source[1],
                    source[2],
                    int(source[3]) + 1,
                    source[4],
                    source[5],
                    source[6],
                    source[7],
                    source[8],
                    not value.report_eligible,
                    value.report_eligible,
                    value.review_status,
                    actor,
                    role,
                    delegation_id,
                    reviewed_at,
                    source[9],
                    source[10],
                    source_id,
                    _json(metadata),
                ),
            )
            return self._inserted_id(cursor, "reviewed legal analysis")

    def append_annotation(
        self,
        value: LegalAnnotationInput,
        *,
        actor_id: str,
    ) -> str:
        actor = self._actor(actor_id)
        annotation_id = f"legal-annotation:{uuid4()}"
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO project_legal_annotations (
                    project_legal_annotation_id, tenant_id, project_id,
                    project_legal_matter_id, legal_resource_id,
                    legal_resource_version_id, legal_provision_id,
                    exact_locator, annotation_text_sha256, annotation_type,
                    status, created_by, created_at,
                    supersedes_annotation_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb
                )
                RETURNING project_legal_annotation_id
                """,
                (
                    annotation_id,
                    self.tenant_id,
                    self.project_id,
                    value.project_legal_matter_id,
                    value.legal_resource_id,
                    value.legal_resource_version_id,
                    value.legal_provision_id,
                    value.exact_locator,
                    _sha256(value.annotation_text_sha256),
                    value.annotation_type,
                    value.status,
                    actor,
                    self._now(),
                    value.supersedes_annotation_id,
                    _json(value.metadata),
                ),
            )
            return self._inserted_id(cursor, "legal annotation")

    def propose_tenant_term_alias(
        self,
        value: TenantLegalTermAliasInput,
        *,
        actor_id: str,
    ) -> str:
        actor = self._actor(actor_id)
        if value.status != "candidate":
            raise ValueError("new tenant aliases must enter as candidates")
        alias_id = f"tenant-legal-term-alias:{uuid4()}"
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tenant_legal_term_aliases (
                    tenant_legal_term_alias_id, tenant_id, term_id, alias_version,
                    alias_text, normalized_alias, status, proposed_by,
                    proposed_at, reviewed_by, reviewed_at,
                    supersedes_alias_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'candidate', %s, %s, NULL, NULL,
                    %s, %s::jsonb
                )
                RETURNING tenant_legal_term_alias_id
                """,
                (
                    alias_id,
                    self.tenant_id,
                    value.term_id,
                    value.alias_version,
                    value.alias_text,
                    value.normalized_alias.casefold(),
                    actor,
                    self._now(),
                    value.supersedes_alias_id,
                    _json(value.metadata),
                ),
            )
            return self._inserted_id(cursor, "tenant legal term alias")

    def review_tenant_term_alias(
        self,
        alias_id: str,
        value: TenantLegalTermAliasReviewInput,
        *,
        actor_id: str,
        actor_role: str,
    ) -> dict[str, str]:
        actor = self._actor(actor_id)
        role = str(actor_role or "").strip().casefold()
        if role not in {
            "legal_reviewer",
            "legal_counsel_approver",
            "external_counsel",
        }:
            raise PermissionError("a legal glossary reviewer role is required")
        source_id = str(alias_id or "").strip()
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    term_id,
                    alias_version,
                    alias_text,
                    normalized_alias,
                    proposed_by,
                    proposed_at,
                    metadata_json
                FROM tenant_legal_term_aliases
                WHERE tenant_legal_term_alias_id = %s
                  AND tenant_id = %s
                  AND status = 'candidate'
                """,
                (source_id, self.tenant_id),
            )
            source = cursor.fetchone()
            if source is None:
                raise LookupError("tenant legal term alias candidate not found")
            if str(source[4]) == actor:
                raise ValueError(
                    "maker-checker separation forbids self-review of an alias"
                )
            timestamp = self._now()
            event_id = f"tenant-legal-term-alias-review:{uuid4()}"
            cursor.execute(
                """
                INSERT INTO tenant_legal_term_alias_review_events (
                    tenant_legal_term_alias_review_event_id,
                    tenant_id,
                    tenant_legal_term_alias_id,
                    review_status,
                    reviewer_id,
                    reviewed_at,
                    reason,
                    metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                RETURNING tenant_legal_term_alias_review_event_id
                """,
                (
                    event_id,
                    self.tenant_id,
                    source_id,
                    value.review_status,
                    actor,
                    timestamp,
                    value.reason,
                    _json(value.metadata),
                ),
            )
            self._inserted_id(cursor, "tenant legal term alias review")

            successor_id = f"tenant-legal-term-alias:{uuid4()}"
            successor_status = (
                value.review_status
                if value.review_status in {"approved", "rejected"}
                else "candidate"
            )
            source_metadata = (
                dict(source[6]) if isinstance(source[6], Mapping) else {}
            )
            source_metadata["latestReviewEventId"] = event_id
            source_metadata["latestReviewReason"] = value.reason
            cursor.execute(
                """
                INSERT INTO tenant_legal_term_aliases (
                    tenant_legal_term_alias_id, tenant_id, term_id,
                    alias_version, alias_text, normalized_alias, status,
                    proposed_by, proposed_at, reviewed_by, reviewed_at,
                    supersedes_alias_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb
                )
                RETURNING tenant_legal_term_alias_id
                """,
                (
                    successor_id,
                    self.tenant_id,
                    source[0],
                    int(source[1]) + 1,
                    source[2],
                    source[3],
                    successor_status,
                    source[4],
                    source[5],
                    actor,
                    timestamp,
                    source_id,
                    _json(source_metadata),
                ),
            )
            self._inserted_id(cursor, "reviewed tenant legal term alias")
            return {
                "tenantLegalTermAliasId": successor_id,
                "reviewEventId": event_id,
                "status": successor_status,
            }

    def list_workspace(self, *, limit: int = 200) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 500))
        with self._cursor() as cursor:
            return {
                "matters": self._rows(
                    cursor,
                    "project_legal_matters",
                    "opened_at",
                    bounded_limit,
                ),
                "waivers": self._rows(
                    cursor,
                    "legal_waivers",
                    "proposed_at",
                    bounded_limit,
                ),
                "counselOpinions": self._rows(
                    cursor,
                    "counsel_opinions",
                    "authored_at",
                    bounded_limit,
                ),
                "analysisVersions": self._rows(
                    cursor,
                    "legal_analysis_versions",
                    "created_at",
                    bounded_limit,
                ),
                "annotations": self._rows(
                    cursor,
                    "project_legal_annotations",
                    "created_at",
                    bounded_limit,
                ),
                "tenantTermAliases": self._tenant_alias_rows(
                    cursor,
                    bounded_limit,
                ),
                "approvalDelegations": self._rows(
                    cursor,
                    "legal_approval_delegations",
                    "created_at",
                    bounded_limit,
                ),
                "requiredClaimSets": self._required_claim_set_rows(
                    cursor,
                    bounded_limit,
                ),
            }

    def _rows(
        self,
        cursor: Any,
        table: str,
        order_column: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        projections = {
            "legal_approval_delegations": (
                "created_at",
                "legal_approval_delegation_id, tenant_id, project_id, "
                "delegation_key, delegation_version, grantor_id, "
                "grantor_role, grantee_id, grantee_role, scope, "
                "valid_from, valid_until, status, reason, "
                "supersedes_delegation_id, created_at",
            ),
            "project_legal_matters": (
                "opened_at",
                "project_legal_matter_id, tenant_id, project_id, "
                "matter_key, matter_version, title, matter_type, status, "
                "severity, owner_user_id, due_at, opened_at, closed_at, "
                "supersedes_matter_id, created_by, created_at",
            ),
            "legal_waivers": (
                "proposed_at",
                "legal_waiver_id, tenant_id, project_id, "
                "project_legal_matter_id, legal_evidence_snapshot_id, "
                "waiver_version, status, reason, conditions_json, "
                "valid_from, valid_until, cure_status, "
                "cure_evidence_snapshot_id, proposed_by, proposed_at, "
                "approved_by, approver_role, "
                "legal_approval_delegation_id, approved_at, "
                "cure_reviewed_by, cure_reviewer_role, cure_reviewed_at, "
                "cure_reason, cure_condition_outcomes_json, "
                "cure_approval_delegation_id, supersedes_waiver_id",
            ),
            "counsel_opinions": (
                "authored_at",
                "counsel_opinion_id, tenant_id, project_id, "
                "project_legal_matter_id, legal_evidence_snapshot_id, "
                "opinion_version, status, content_sha256, privileged, "
                "authored_by, authored_at, approved_by, approver_role, "
                "legal_approval_delegation_id, approved_at, "
                "supersedes_opinion_id, TRUE AS protected_object_present",
            ),
            "legal_analysis_versions": (
                "created_at",
                "legal_analysis_version_id, tenant_id, project_id, "
                "project_legal_matter_id, legal_evidence_snapshot_id, "
                "analysis_kind, analysis_version, content_sha256, "
                "input_snapshot_sha256, model_id, prompt_sha256, "
                "analysis_only, report_eligible, human_review_status, "
                "reviewed_by, reviewer_role, "
                "legal_approval_delegation_id, reviewed_at, created_by, "
                "created_at, "
                "supersedes_analysis_version_id, "
                "TRUE AS protected_object_present",
            ),
            "project_legal_annotations": (
                "created_at",
                "project_legal_annotation_id, tenant_id, project_id, "
                "project_legal_matter_id, legal_resource_id, "
                "legal_resource_version_id, legal_provision_id, "
                "exact_locator, annotation_text_sha256, annotation_type, "
                "status, created_by, created_at, supersedes_annotation_id",
            ),
        }
        projection = projections.get(table)
        if projection is None or projection[0] != order_column:
            raise ValueError("unsupported governance table")
        selected_columns = projection[1]
        cursor.execute(
            f"""
            SELECT row_to_json(item)
            FROM (
                SELECT {selected_columns}
                FROM {table}
                WHERE tenant_id = %s AND project_id = %s
                ORDER BY {order_column} DESC
                LIMIT %s
            ) AS item
            """,
            (self.tenant_id, self.project_id, limit),
        )
        values: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            value = row[0]
            if isinstance(value, Mapping):
                values.append(dict(value))
            elif isinstance(value, str):
                values.append(json.loads(value))
        return values

    def _tenant_alias_rows(
        self,
        cursor: Any,
        limit: int,
    ) -> list[dict[str, Any]]:
        cursor.execute(
            """
            SELECT row_to_json(item)
            FROM (
                SELECT *
                FROM tenant_legal_term_aliases
                WHERE tenant_id = %s
                ORDER BY proposed_at DESC, alias_version DESC
                LIMIT %s
            ) AS item
            """,
            (self.tenant_id, limit),
        )
        values: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            value = row[0]
            if isinstance(value, Mapping):
                values.append(dict(value))
            elif isinstance(value, str):
                values.append(json.loads(value))
        return values

    def _required_claim_set_rows(
        self,
        cursor: Any,
        limit: int,
    ) -> list[dict[str, Any]]:
        cursor.execute(
            """
            SELECT jsonb_build_object(
                'legalRequiredClaimSetId',
                    claim_set.legal_required_claim_set_id,
                'tenantId', claim_set.tenant_id,
                'projectId', claim_set.project_id,
                'claimSetKey', claim_set.claim_set_key,
                'claimSetVersion', claim_set.claim_set_version,
                'reviewScope', claim_set.review_scope,
                'status', claim_set.status,
                'claimSetSha256', claim_set.claim_set_sha256,
                'createdBy', claim_set.created_by,
                'createdAt', claim_set.created_at,
                'approvedBy', claim_set.approved_by,
                'approverRole', claim_set.approver_role,
                'legalApprovalDelegationId',
                    claim_set.legal_approval_delegation_id,
                'approvedAt', claim_set.approved_at,
                'supersedesClaimSetId',
                    claim_set.supersedes_claim_set_id,
                'claims',
                    COALESCE(
                        jsonb_agg(
                            jsonb_build_object(
                                'claimKey', claim.claim_key,
                                'claimTextSha256',
                                    claim.claim_text_sha256,
                                'description', claim.description,
                                'ordinal', claim.ordinal
                            )
                            ORDER BY claim.ordinal, claim.claim_key
                        ) FILTER (
                            WHERE claim.legal_required_claim_id IS NOT NULL
                        ),
                        '[]'::jsonb
                    )
            )
            FROM legal_required_claim_sets AS claim_set
            LEFT JOIN legal_required_claims AS claim
              ON claim.legal_required_claim_set_id =
                     claim_set.legal_required_claim_set_id
             AND claim.tenant_id = claim_set.tenant_id
             AND claim.project_id = claim_set.project_id
            WHERE claim_set.tenant_id = %s
              AND claim_set.project_id = %s
            GROUP BY claim_set.legal_required_claim_set_id
            ORDER BY claim_set.created_at DESC,
                     claim_set.claim_set_version DESC
            LIMIT %s
            """,
            (self.tenant_id, self.project_id, limit),
        )
        values: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            value = row[0]
            if isinstance(value, Mapping):
                values.append(dict(value))
            elif isinstance(value, str):
                values.append(json.loads(value))
        return values

    class _CursorContext:
        def __init__(self, repository: "PostgresLegalGovernanceRepository"):
            self.repository = repository
            self.connection: Any = None
            self.cursor: Any = None

        def __enter__(self) -> Any:
            self.connection = self.repository._connect()
            self.connection.__enter__()
            self.cursor = self.connection.cursor()
            self.cursor.__enter__()
            self.cursor.execute(
                f'SET LOCAL search_path TO "{self.repository._schema}", '
                "pg_catalog"
            )
            self.cursor.execute(
                "SELECT set_config('realytics.tenant_id', %s, true)",
                (self.repository.tenant_id,),
            )
            return self.cursor

        def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
            suppress = self.cursor.__exit__(exc_type, exc, traceback)
            connection_suppress = self.connection.__exit__(
                exc_type,
                exc,
                traceback,
            )
            return bool(suppress or connection_suppress)

    def _cursor(self) -> "_CursorContext":
        return self._CursorContext(self)

    def _actor(self, actor_id: str) -> str:
        actor = str(actor_id or "").strip()
        if not actor or actor.casefold() in {"anonymous", "unknown", "system"}:
            raise ValueError("actor_id must be an authenticated human principal")
        return actor

    def _final_counsel(self, actor_id: str, actor_role: str) -> str:
        actor = self._actor(actor_id)
        role = str(actor_role or "").strip().casefold()
        if role not in FINAL_COUNSEL_ROLES:
            raise PermissionError(
                "final legal approval requires legal_counsel_approver or "
                "external_counsel"
            )
        return actor

    def _validate_delegated_authority(
        self,
        cursor: Any,
        *,
        actor_id: str,
        actor_role: str,
        scope: str,
        delegation_id: str | None,
        at: datetime | None = None,
    ) -> str | None:
        """Validate an external counsel delegation in the current transaction.

        Native legal counsel authority is role-bound and must not be
        represented by an unrelated delegation identifier.  External counsel
        authority is always explicit, project-scoped, time-bound and leaf-only.
        """

        actor = self._actor(actor_id)
        role = str(actor_role or "").strip().casefold()
        identifier = str(delegation_id or "").strip()
        if role != "external_counsel":
            if identifier:
                raise ValueError(
                    "legalApprovalDelegationId is only valid for "
                    "external_counsel"
                )
            return None
        if not identifier:
            raise PermissionError(
                "external_counsel requires legalApprovalDelegationId"
            )
        timestamp = self._timestamp(at)
        cursor.execute(
            """
            SELECT delegation.legal_approval_delegation_id
            FROM legal_approval_delegations AS delegation
            WHERE delegation.legal_approval_delegation_id = %s
              AND delegation.tenant_id = %s
              AND delegation.project_id = %s
              AND delegation.grantee_id = %s
              AND delegation.grantee_role = %s
              AND delegation.scope = %s
              AND delegation.status = 'active'
              AND delegation.created_at <= %s
              AND delegation.valid_from <= %s
              AND delegation.valid_until >= %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM legal_approval_delegations AS successor
                  WHERE successor.tenant_id = delegation.tenant_id
                    AND successor.project_id = delegation.project_id
                    AND successor.delegation_key =
                        delegation.delegation_key
                    AND successor.delegation_version >
                        delegation.delegation_version
              )
            FOR SHARE
            """,
            (
                identifier,
                self.tenant_id,
                self.project_id,
                actor,
                role,
                str(scope or "").strip(),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        row = cursor.fetchone()
        if row is None or str(row[0] or "").strip() != identifier:
            raise PermissionError(
                "active project-scoped legal approval delegation not found"
            )
        return identifier

    def _validate_current_evidence_snapshot(
        self,
        cursor: Any,
        snapshot_id: str,
    ) -> None:
        identifier = str(snapshot_id or "").strip()
        if not identifier:
            raise ValueError("cureEvidenceSnapshotId is required")
        cursor.execute(
            """
            SELECT snapshot.legal_evidence_snapshot_id
            FROM legal_evidence_snapshots AS snapshot
            WHERE snapshot.legal_evidence_snapshot_id = %s
              AND snapshot.tenant_id = %s
              AND snapshot.project_id = %s
              AND snapshot.gate_eligible
              AND snapshot.citation_verification_status = 'verified'
              AND NOT snapshot.has_open_conflicts
              AND NOT snapshot.is_stale
              AND NOT EXISTS (
                  SELECT 1
                  FROM legal_evidence_snapshots AS successor
                  WHERE successor.tenant_id = snapshot.tenant_id
                    AND successor.project_id = snapshot.project_id
                    AND successor.supersedes_snapshot_id =
                        snapshot.legal_evidence_snapshot_id
              )
            FOR SHARE
            """,
            (identifier, self.tenant_id, self.project_id),
        )
        row = cursor.fetchone()
        if row is None or str(row[0] or "").strip() != identifier:
            raise ValueError(
                "cure evidence must reference a current verified "
                "project snapshot"
            )

    def _validate_cure_evidence_items(
        self,
        cursor: Any,
        *,
        snapshot_id: str,
        evidence_item_ids: list[str],
    ) -> None:
        """Require every cure item to retain exact, current corpus lineage."""

        if not evidence_item_ids:
            return
        cursor.execute(
            """
            SELECT COUNT(DISTINCT requested.legal_evidence_item_id)
            FROM unnest(%s::text[]) AS requested(legal_evidence_item_id)
            JOIN legal_evidence_snapshot_items AS item
              ON item.legal_evidence_item_id =
                     requested.legal_evidence_item_id
             AND item.legal_evidence_snapshot_id = %s
             AND item.tenant_id = %s
             AND item.project_id = %s
             AND item.citation_verification_status = 'verified'
             AND item.verified_at IS NOT NULL
             AND item.verified_at <= statement_timestamp()
            JOIN legal_resource_versions AS version
              ON version.legal_resource_version_id =
                     item.legal_resource_version_id
             AND version.legal_resource_id = item.legal_resource_id
             AND version.source_fetch_id = item.source_fetch_id
             AND version.verification_status IN (
                 'source_verified',
                 'schema_verified'
             )
             AND version.verified_at IS NOT NULL
             AND version.verified_at <= statement_timestamp()
            JOIN legal_resources AS resource
              ON resource.legal_resource_id = item.legal_resource_id
             AND resource.provider_id = version.provider_id
            JOIN legal_resource_version_lineages AS lineage
              ON lineage.legal_resource_version_id =
                     version.legal_resource_version_id
             AND lineage.legal_resource_id = version.legal_resource_id
             AND lineage.validated_at <= statement_timestamp()
            JOIN legal_source_fetches AS source_fetch
              ON source_fetch.source_fetch_id = item.source_fetch_id
             AND source_fetch.provider_id = version.provider_id
             AND source_fetch.fetch_status IN (
                 'succeeded',
                 'not_modified'
             )
             AND source_fetch.fetched_at <= statement_timestamp()
            JOIN legal_source_providers AS provider
              ON provider.provider_id = version.provider_id
             AND provider.provider_status = 'active'
            JOIN legal_provisions AS provision
              ON provision.legal_provision_id = item.legal_provision_id
             AND provision.legal_resource_version_id =
                     item.legal_resource_version_id
             AND provision.legal_resource_id = item.legal_resource_id
             AND provision.source_locator = item.exact_locator
            WHERE NOT EXISTS (
                SELECT 1
                FROM legal_resource_versions AS replacement
                JOIN legal_source_fetches AS replacement_fetch
                  ON replacement_fetch.source_fetch_id =
                         replacement.source_fetch_id
                 AND replacement_fetch.provider_id =
                         replacement.provider_id
                WHERE replacement.legal_resource_id =
                          item.legal_resource_id
                  AND replacement.version_sequence >
                          version.version_sequence
                  AND replacement.verification_status IN (
                      'source_verified',
                      'schema_verified'
                  )
                  AND replacement.verified_at IS NOT NULL
                  AND replacement.verified_at <= statement_timestamp()
                  AND replacement_fetch.fetch_status IN (
                      'succeeded',
                      'not_modified'
                  )
            )
            """,
            (
                evidence_item_ids,
                snapshot_id,
                self.tenant_id,
                self.project_id,
            ),
        )
        row = cursor.fetchone()
        if row is None or int(row[0] or 0) != len(evidence_item_ids):
            raise ValueError(
                "each cure evidenceItemId must be a verified exact/current "
                "item in the current cure snapshot"
            )

    @staticmethod
    def _condition_due_at(value: Any) -> datetime | None:
        if not isinstance(value, Mapping):
            return None
        raw = value.get("dueAt")
        if raw is None:
            raw = value.get("due_at")
        if raw is None or isinstance(raw, datetime):
            return raw
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError(
                "waiver condition dueAt must include a timezone"
            )
        return parsed

    def _timestamp(self, value: datetime | None) -> datetime:
        timestamp = value or self._now()
        if timestamp.tzinfo is None:
            raise ValueError("governance timestamps must include a timezone")
        return timestamp

    @staticmethod
    def _inserted_id(cursor: Any, label: str) -> str:
        row = cursor.fetchone()
        if row is None or not str(row[0] or "").strip():
            raise ValueError(f"{label} was not inserted")
        return str(row[0])


__all__ = [
    "FINAL_COUNSEL_ROLES",
    "PostgresLegalGovernanceRepository",
]
