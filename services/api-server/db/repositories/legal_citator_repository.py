"""Tenant-native PostgreSQL persistence for human Citator review.

The institution-wide ``legal_citations`` and ``legal_relation_assertions``
tables are immutable Corpus facts.  A project review therefore appends a
tenant/project-scoped review version and its matching event; it never inserts
a project opinion into either global Corpus table.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from db.legal_review_database import connect_legal_review_database
from db.postgres_runtime_privileges import validate_database_identifier
from legal_intelligence.citator_models import (
    CitatorEvidenceBinding,
    CitatorRecordKind,
    CitatorReviewInput,
    CitatorReviewOutcome,
    CitatorReviewStatus,
)


ConnectionFactory = Callable[[], Any]
FINAL_COUNSEL_ROLES = frozenset(
    {"legal_counsel_approver", "external_counsel"}
)
REVIEWER_ROLES = frozenset(
    {"legal_reviewer", "legal_counsel_approver", "external_counsel"}
)
FINAL_REVIEW_STATUSES = frozenset(
    {CitatorReviewStatus.APPROVED, CitatorReviewStatus.REJECTED}
)
_MAKER_KEYS = ("assertedBy", "createdBy", "makerId")


@dataclass(frozen=True)
class _EvidenceBinding:
    snapshot_id: str
    snapshot_version: int
    snapshot_sha256: str
    snapshot_as_of_date: Any
    evidence_item_id: str
    resource_version_id: str
    provision_id: str
    exact_locator: str
    source_fetch_id: str
    provider_id: str
    excerpt_sha256: str
    provision_sha256: str
    provision_body: str
    verified_at: datetime
    source_authority: str
    authority_tier: str
    freshness_policy_version: str

    @classmethod
    def from_row(
        cls,
        row: tuple[Any, ...],
        *,
        offset: int = 12,
    ) -> "_EvidenceBinding":
        values = tuple(row[offset : offset + 17])
        if len(values) != 17 or any(value is None for value in values):
            raise LookupError(
                "current project evidence binding is incomplete"
            )
        return cls(
            snapshot_id=str(values[0]),
            snapshot_version=int(values[1]),
            snapshot_sha256=str(values[2]),
            snapshot_as_of_date=values[3],
            evidence_item_id=str(values[4]),
            resource_version_id=str(values[5]),
            provision_id=str(values[6]),
            exact_locator=str(values[7]),
            source_fetch_id=str(values[8]),
            provider_id=str(values[9]),
            excerpt_sha256=str(values[10]),
            provision_sha256=str(values[11]),
            provision_body=str(values[12]),
            verified_at=values[13],
            source_authority=str(values[14]),
            authority_tier=str(values[15]),
            freshness_policy_version=str(values[16]),
        )

    def public(self) -> CitatorEvidenceBinding:
        return CitatorEvidenceBinding(
            snapshotId=self.snapshot_id,
            snapshotVersion=self.snapshot_version,
            snapshotSha256=self.snapshot_sha256,
            snapshotAsOfDate=self.snapshot_as_of_date,
            evidenceItemId=self.evidence_item_id,
            resourceVersionId=self.resource_version_id,
            provisionId=self.provision_id,
            exactLocator=self.exact_locator,
            sourceFetchId=self.source_fetch_id,
            providerId=self.provider_id,
            excerptSha256=self.excerpt_sha256,
            provisionSha256=self.provision_sha256,
            verifiedAt=self.verified_at,
            sourceAuthority=self.source_authority,
            authorityTier=self.authority_tier,
            freshnessPolicyVersion=self.freshness_policy_version,
            current=True,
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class PostgresLegalCitatorRepository:
    """Review immutable global candidates through a tenant-native overlay."""

    def __init__(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_factory: ConnectionFactory | None = None,
        review_connection_factory: ConnectionFactory = (
            connect_legal_review_database
        ),
        schema_name: str | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.tenant_id = str(tenant_id or "").strip()
        self.project_id = str(project_id or "").strip()
        if not self.tenant_id or not self.project_id:
            raise ValueError("tenant_id and project_id are required")
        self._review_connect = (
            connection_factory or review_connection_factory
        )
        self._schema = validate_database_identifier(
            schema_name
            or os.environ.get("REALYTICS_DATABASE_SCHEMA")
            or "public",
            label="database schema",
        )
        self._now = now

    def list_review_queue(
        self,
        *,
        record_kind: CitatorRecordKind | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 500))
        with self._review_cursor() as cursor:
            citations = (
                self._list_citations(cursor, bounded_limit)
                if record_kind in {None, CitatorRecordKind.CITATION}
                else []
            )
            relations = (
                self._list_relations(cursor, bounded_limit)
                if record_kind in {None, CitatorRecordKind.RELATION}
                else []
            )
        return {
            "citations": citations,
            "relations": relations,
            "totalCount": len(citations) + len(relations),
        }

    def review_citation(
        self,
        citation_id: str,
        review: CitatorReviewInput,
        *,
        reviewer_id: str,
        reviewer_role: str,
    ) -> CitatorReviewOutcome:
        if review.object_evidence_item_id is not None:
            raise ValueError(
                "objectEvidenceItemId is only valid for a relation review"
            )
        reviewer, role = self._reviewer(
            reviewer_id,
            reviewer_role,
            final=review.review_status in FINAL_REVIEW_STATUSES,
        )
        source_id = self._record_id(citation_id, "legal citation")
        reviewed_at = self._now()
        excerpt, excerpt_hash = self._review_evidence(review)
        review_version_id = f"project-legal-citation-review:{uuid4()}"
        event_id = f"project-legal-citation-review-event:{uuid4()}"
        final_status, human_status = self._citation_statuses(
            review.review_status
        )

        with self._review_cursor() as cursor:
            self._lock_record(
                cursor,
                CitatorRecordKind.CITATION,
                source_id,
            )
            source = self._citation_for_review(
                cursor,
                source_id,
                required_excerpt_sha256=(
                    excerpt_hash
                    if review.review_status == CitatorReviewStatus.APPROVED
                    else None
                ),
            )
            binding = _EvidenceBinding.from_row(source)
            self._validate_approved_binding(
                review,
                excerpt=excerpt,
                excerpt_hash=excerpt_hash,
                binding=binding,
            )
            maker_id = self._source_maker(
                source[10],
                assertion_method=source[7],
            )
            self._assert_maker_checker(maker_id, reviewer)
            latest = self._latest_citation_review(cursor, source_id)
            review_version, predecessor_id, predecessor_event_id = (
                self._review_chain(latest, review.supersedes_review_event_id)
            )
            delegation_id = self._validate_review_authority(
                cursor,
                review=review,
                reviewer_id=reviewer,
                reviewer_role=role,
                reviewed_at=reviewed_at,
            )
            cursor.execute(
                """
                INSERT INTO project_legal_citation_review_versions (
                    project_legal_citation_review_version_id,
                    tenant_id, project_id, legal_citation_id,
                    legal_evidence_snapshot_id, legal_evidence_item_id,
                    legal_resource_version_id, legal_provision_id,
                    source_fetch_id, provider_id, evidence_exact_locator,
                    bound_excerpt_sha256, bound_provision_sha256,
                    review_version,
                    review_status, effective_citation_status,
                    human_review_status, maker_id, reviewer_id, reviewer_role,
                    reviewed_at, reason, evidence_excerpt,
                    evidence_excerpt_sha256,
                    legal_approval_delegation_id,
                    supersedes_review_version_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb
                )
                RETURNING project_legal_citation_review_version_id
                """,
                (
                    review_version_id,
                    self.tenant_id,
                    self.project_id,
                    source_id,
                    binding.snapshot_id,
                    binding.evidence_item_id,
                    binding.resource_version_id,
                    binding.provision_id,
                    binding.source_fetch_id,
                    binding.provider_id,
                    binding.exact_locator,
                    binding.excerpt_sha256,
                    binding.provision_sha256,
                    review_version,
                    review.review_status.value,
                    final_status,
                    human_status,
                    maker_id,
                    reviewer,
                    role,
                    reviewed_at,
                    review.reason,
                    excerpt,
                    excerpt_hash,
                    delegation_id,
                    predecessor_id,
                    _json(review.metadata),
                ),
            )
            self._inserted_id(cursor, "project citation review version")
            cursor.execute(
                """
                INSERT INTO project_legal_citation_review_events (
                    project_legal_citation_review_event_id,
                    tenant_id, project_id,
                    project_legal_citation_review_version_id,
                    legal_citation_id,
                    legal_evidence_snapshot_id, legal_evidence_item_id,
                    legal_resource_version_id, legal_provision_id,
                    source_fetch_id, provider_id, evidence_exact_locator,
                    bound_excerpt_sha256, bound_provision_sha256,
                    event_sequence, review_status,
                    reviewer_id, reviewer_role, reviewed_at, reason,
                    evidence_excerpt_sha256,
                    legal_approval_delegation_id,
                    supersedes_review_event_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb
                )
                RETURNING project_legal_citation_review_event_id
                """,
                (
                    event_id,
                    self.tenant_id,
                    self.project_id,
                    review_version_id,
                    source_id,
                    binding.snapshot_id,
                    binding.evidence_item_id,
                    binding.resource_version_id,
                    binding.provision_id,
                    binding.source_fetch_id,
                    binding.provider_id,
                    binding.exact_locator,
                    binding.excerpt_sha256,
                    binding.provision_sha256,
                    review_version,
                    review.review_status.value,
                    reviewer,
                    role,
                    reviewed_at,
                    review.reason,
                    excerpt_hash,
                    delegation_id,
                    predecessor_event_id,
                    _json(review.metadata),
                ),
            )
            self._inserted_id(cursor, "project citation review event")

        return CitatorReviewOutcome(
            recordKind=CitatorRecordKind.CITATION,
            sourceRecordId=source_id,
            reviewVersionId=review_version_id,
            reviewVersion=review_version,
            reviewEventId=event_id,
            reviewStatus=review.review_status,
            finalStatus=final_status,
            reviewerId=reviewer,
            reviewerRole=role,
            reviewedAt=reviewed_at,
            evidenceExcerptSha256=excerpt_hash,
            legalApprovalDelegationId=delegation_id,
            evidenceBinding=binding.public(),
        )

    def review_relation(
        self,
        assertion_id: str,
        review: CitatorReviewInput,
        *,
        reviewer_id: str,
        reviewer_role: str,
    ) -> CitatorReviewOutcome:
        reviewer, role = self._reviewer(
            reviewer_id,
            reviewer_role,
            final=review.review_status in FINAL_REVIEW_STATUSES,
        )
        source_id = self._record_id(
            assertion_id,
            "legal relation assertion",
        )
        reviewed_at = self._now()
        excerpt, excerpt_hash = self._review_evidence(review)
        review_version_id = f"project-legal-relation-review:{uuid4()}"
        event_id = f"project-legal-relation-review-event:{uuid4()}"
        final_status, human_status = self._relation_statuses(
            review.review_status
        )

        with self._review_cursor() as cursor:
            self._lock_record(
                cursor,
                CitatorRecordKind.RELATION,
                source_id,
            )
            source = self._relation_for_review(
                cursor,
                source_id,
                required_excerpt_sha256=(
                    excerpt_hash
                    if review.review_status == CitatorReviewStatus.APPROVED
                    else None
                ),
            )
            binding = _EvidenceBinding.from_row(source)
            object_item_id = str(
                review.object_evidence_item_id or ""
            ).strip()
            if (
                review.review_status == CitatorReviewStatus.APPROVED
                and not object_item_id
            ):
                raise ValueError(
                    "approved relation review requires "
                    "objectEvidenceItemId"
                )
            object_binding = (
                self._object_binding_for_review(
                    cursor,
                    snapshot_id=binding.snapshot_id,
                    object_resource_id=str(source[1]),
                    evidence_item_id=object_item_id,
                )
                if object_item_id
                else None
            )
            self._validate_approved_binding(
                review,
                excerpt=excerpt,
                excerpt_hash=excerpt_hash,
                binding=binding,
            )
            maker_id = self._source_maker(
                source[11],
                assertion_method=source[5],
            )
            self._assert_maker_checker(maker_id, reviewer)
            latest = self._latest_relation_review(cursor, source_id)
            review_version, predecessor_id, predecessor_event_id = (
                self._review_chain(latest, review.supersedes_review_event_id)
            )
            delegation_id = self._validate_review_authority(
                cursor,
                review=review,
                reviewer_id=reviewer,
                reviewer_role=role,
                reviewed_at=reviewed_at,
            )
            cursor.execute(
                """
                INSERT INTO project_legal_relation_review_versions (
                    project_legal_relation_review_version_id,
                    tenant_id, project_id, legal_relation_assertion_id,
                    legal_evidence_snapshot_id, legal_evidence_item_id,
                    legal_resource_version_id, legal_provision_id,
                    source_fetch_id, provider_id, evidence_exact_locator,
                    bound_excerpt_sha256, bound_provision_sha256,
                    object_legal_evidence_item_id,
                    object_legal_resource_version_id,
                    object_legal_provision_id, object_source_fetch_id,
                    object_provider_id, object_evidence_exact_locator,
                    object_bound_excerpt_sha256,
                    object_bound_provision_sha256,
                    review_version, review_status,
                    effective_assertion_status, human_review_status,
                    maker_id, reviewer_id, reviewer_role, reviewed_at, reason,
                    evidence_excerpt, evidence_excerpt_sha256,
                    legal_approval_delegation_id,
                    supersedes_review_version_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                RETURNING project_legal_relation_review_version_id
                """,
                (
                    review_version_id,
                    self.tenant_id,
                    self.project_id,
                    source_id,
                    binding.snapshot_id,
                    binding.evidence_item_id,
                    binding.resource_version_id,
                    binding.provision_id,
                    binding.source_fetch_id,
                    binding.provider_id,
                    binding.exact_locator,
                    binding.excerpt_sha256,
                    binding.provision_sha256,
                    object_binding.evidence_item_id
                    if object_binding
                    else None,
                    object_binding.resource_version_id
                    if object_binding
                    else None,
                    object_binding.provision_id if object_binding else None,
                    object_binding.source_fetch_id
                    if object_binding
                    else None,
                    object_binding.provider_id if object_binding else None,
                    object_binding.exact_locator if object_binding else None,
                    object_binding.excerpt_sha256
                    if object_binding
                    else None,
                    object_binding.provision_sha256
                    if object_binding
                    else None,
                    review_version,
                    review.review_status.value,
                    final_status,
                    human_status,
                    maker_id,
                    reviewer,
                    role,
                    reviewed_at,
                    review.reason,
                    excerpt,
                    excerpt_hash,
                    delegation_id,
                    predecessor_id,
                    _json(review.metadata),
                ),
            )
            self._inserted_id(cursor, "project relation review version")
            cursor.execute(
                """
                INSERT INTO project_legal_relation_review_events (
                    project_legal_relation_review_event_id,
                    tenant_id, project_id,
                    project_legal_relation_review_version_id,
                    legal_relation_assertion_id,
                    legal_evidence_snapshot_id, legal_evidence_item_id,
                    legal_resource_version_id, legal_provision_id,
                    source_fetch_id, provider_id, evidence_exact_locator,
                    bound_excerpt_sha256, bound_provision_sha256,
                    object_legal_evidence_item_id,
                    object_legal_resource_version_id,
                    object_legal_provision_id, object_source_fetch_id,
                    object_provider_id, object_evidence_exact_locator,
                    object_bound_excerpt_sha256,
                    object_bound_provision_sha256,
                    event_sequence, review_status,
                    reviewer_id, reviewer_role, reviewed_at, reason,
                    evidence_excerpt_sha256,
                    legal_approval_delegation_id,
                    supersedes_review_event_id, metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                RETURNING project_legal_relation_review_event_id
                """,
                (
                    event_id,
                    self.tenant_id,
                    self.project_id,
                    review_version_id,
                    source_id,
                    binding.snapshot_id,
                    binding.evidence_item_id,
                    binding.resource_version_id,
                    binding.provision_id,
                    binding.source_fetch_id,
                    binding.provider_id,
                    binding.exact_locator,
                    binding.excerpt_sha256,
                    binding.provision_sha256,
                    object_binding.evidence_item_id
                    if object_binding
                    else None,
                    object_binding.resource_version_id
                    if object_binding
                    else None,
                    object_binding.provision_id if object_binding else None,
                    object_binding.source_fetch_id
                    if object_binding
                    else None,
                    object_binding.provider_id if object_binding else None,
                    object_binding.exact_locator if object_binding else None,
                    object_binding.excerpt_sha256
                    if object_binding
                    else None,
                    object_binding.provision_sha256
                    if object_binding
                    else None,
                    review_version,
                    review.review_status.value,
                    reviewer,
                    role,
                    reviewed_at,
                    review.reason,
                    excerpt_hash,
                    delegation_id,
                    predecessor_event_id,
                    _json(review.metadata),
                ),
            )
            self._inserted_id(cursor, "project relation review event")

        return CitatorReviewOutcome(
            recordKind=CitatorRecordKind.RELATION,
            sourceRecordId=source_id,
            reviewVersionId=review_version_id,
            reviewVersion=review_version,
            reviewEventId=event_id,
            reviewStatus=review.review_status,
            finalStatus=final_status,
            reviewerId=reviewer,
            reviewerRole=role,
            reviewedAt=reviewed_at,
            evidenceExcerptSha256=excerpt_hash,
            legalApprovalDelegationId=delegation_id,
            evidenceBinding=binding.public(),
            objectEvidenceBinding=(
                object_binding.public() if object_binding else None
            ),
        )

    def _citation_for_review(
        self,
        cursor: Any,
        source_id: str,
        *,
        required_excerpt_sha256: str | None,
    ) -> tuple[Any, ...]:
        cursor.execute(
            """
            WITH current_snapshot AS (
                SELECT snapshot.*
                FROM legal_evidence_snapshots AS snapshot
                WHERE snapshot.tenant_id = %s
                  AND snapshot.project_id = %s
                  AND snapshot.created_at <= statement_timestamp()
                  AND snapshot.as_of_date <= CURRENT_DATE
                  AND snapshot.is_stale = FALSE
                  AND snapshot.freshness_policy_version =
                      'legal-freshness-v1'
                  AND snapshot.citation_verification_status IN (
                      'partial', 'verified'
                  )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM legal_evidence_snapshots AS newer
                        WHERE newer.tenant_id = snapshot.tenant_id
                          AND newer.project_id = snapshot.project_id
                          AND (
                                newer.snapshot_version >
                                    snapshot.snapshot_version
                                OR newer.supersedes_snapshot_id =
                                    snapshot.legal_evidence_snapshot_id
                          )
                  )
                ORDER BY snapshot.snapshot_version DESC,
                         snapshot.created_at DESC,
                         snapshot.legal_evidence_snapshot_id DESC
                LIMIT 1
            )
            SELECT
                citation.source_resource_id,
                citation.source_version_id,
                citation.source_provision_id,
                citation.cited_identifier_text,
                citation.target_resource_id,
                citation.target_version_id,
                citation.target_provision_id,
                citation.assertion_method,
                citation.algorithm_version,
                citation.confidence,
                citation.metadata_json,
                citation.asserted_at,
                snapshot.legal_evidence_snapshot_id,
                snapshot.snapshot_version,
                snapshot.snapshot_sha256,
                snapshot.as_of_date,
                item.legal_evidence_item_id,
                item.legal_resource_version_id,
                item.legal_provision_id,
                item.exact_locator,
                item.source_fetch_id,
                provider.provider_id,
                item.excerpt_sha256,
                provision.provision_sha256,
                provision.body_text,
                item.verified_at,
                provider.source_authority,
                provider.authority_tier,
                snapshot.freshness_policy_version
            FROM legal_citations AS citation
            JOIN current_snapshot AS snapshot ON TRUE
            JOIN legal_evidence_snapshot_items AS item
              ON item.tenant_id = snapshot.tenant_id
             AND item.project_id = snapshot.project_id
             AND item.legal_evidence_snapshot_id =
                 snapshot.legal_evidence_snapshot_id
             AND item.legal_resource_id = citation.source_resource_id
             AND item.legal_resource_version_id =
                 citation.source_version_id
             AND item.legal_provision_id = citation.source_provision_id
            JOIN legal_resource_versions AS version
              ON version.legal_resource_version_id =
                 item.legal_resource_version_id
             AND version.legal_resource_id = item.legal_resource_id
             AND version.source_fetch_id = item.source_fetch_id
            JOIN legal_resources AS resource
              ON resource.legal_resource_id = item.legal_resource_id
             AND resource.provider_id = version.provider_id
            JOIN legal_source_fetches AS source_fetch
              ON source_fetch.source_fetch_id = item.source_fetch_id
             AND source_fetch.provider_id = version.provider_id
            JOIN legal_source_providers AS provider
              ON provider.provider_id = version.provider_id
            JOIN legal_resource_version_lineages AS lineage
              ON lineage.legal_resource_version_id =
                 version.legal_resource_version_id
             AND lineage.legal_resource_id = version.legal_resource_id
            JOIN legal_provisions AS provision
              ON provision.legal_provision_id = item.legal_provision_id
             AND provision.legal_resource_version_id =
                 item.legal_resource_version_id
             AND provision.legal_resource_id = item.legal_resource_id
            WHERE citation.legal_citation_id = %s
              AND citation.citation_status IN (
                    'unknown', 'unresolved', 'ambiguous', 'resolved'
              )
              AND NULLIF(
                    btrim(citation.metadata_json ->> 'reviewStatus'),
                    ''
              ) IS NULL
              AND citation.source_provision_id IS NOT NULL
              AND item.citation_verification_status = 'verified'
              AND item.verified_at IS NOT NULL
              AND item.verified_at <= statement_timestamp()
              AND item.verified_at + INTERVAL '7 days' >=
                  statement_timestamp()
              AND version.verification_status IN (
                    'source_verified', 'schema_verified'
              )
              AND version.verified_at IS NOT NULL
              AND version.verified_at <= statement_timestamp()
              AND source_fetch.fetch_status IN ('succeeded', 'not_modified')
              AND source_fetch.fetched_at <= statement_timestamp()
              AND provider.provider_status = 'active'
              AND lineage.validated_at <= statement_timestamp()
              AND provision.source_locator = item.exact_locator
              AND provision.provision_sha256 =
                  encode(
                      extensions.digest(
                          convert_to(provision.body_text, 'UTF8'),
                          'sha256'
                      ),
                      'hex'
                  )
              AND (
                    version.effective_from IS NULL
                    OR version.effective_from <= snapshot.as_of_date
              )
              AND (
                    version.effective_to IS NULL
                    OR version.effective_to >= snapshot.as_of_date
              )
              AND (
                    version.valid_time_start IS NULL
                    OR version.valid_time_start <= snapshot.as_of_date
              )
              AND (
                    version.valid_time_end IS NULL
                    OR version.valid_time_end >= snapshot.as_of_date
              )
              AND (%s IS NULL OR item.excerpt_sha256 = %s)
              AND NOT EXISTS (
                    SELECT 1
                    FROM legal_resource_versions AS replacement
                    JOIN legal_source_fetches AS replacement_fetch
                      ON replacement_fetch.source_fetch_id =
                         replacement.source_fetch_id
                     AND replacement_fetch.provider_id =
                         replacement.provider_id
                    JOIN legal_source_providers AS replacement_provider
                      ON replacement_provider.provider_id =
                         replacement.provider_id
                    WHERE replacement.legal_resource_id =
                          version.legal_resource_id
                      AND replacement.version_sequence >
                          version.version_sequence
                      AND replacement.verification_status IN (
                          'source_verified', 'schema_verified'
                      )
                      AND replacement.verified_at IS NOT NULL
                      AND replacement.verified_at <= statement_timestamp()
                      AND replacement_fetch.fetch_status IN (
                          'succeeded', 'not_modified'
                      )
                      AND replacement_provider.provider_status = 'active'
                      AND (
                          replacement.supersedes_version_id =
                              version.legal_resource_version_id
                          OR (
                              (
                                  replacement.effective_from IS NULL
                                  OR replacement.effective_from <=
                                     snapshot.as_of_date
                              )
                              AND (
                                  replacement.effective_to IS NULL
                                  OR replacement.effective_to >=
                                     snapshot.as_of_date
                              )
                              AND (
                                  replacement.valid_time_start IS NULL
                                  OR replacement.valid_time_start <=
                                     snapshot.as_of_date
                              )
                              AND (
                                  replacement.valid_time_end IS NULL
                                  OR replacement.valid_time_end >=
                                     snapshot.as_of_date
                              )
                          )
                      )
              )
            ORDER BY item.verified_at DESC,
                     item.legal_evidence_item_id ASC
            LIMIT 1
            """,
            (
                self.tenant_id,
                self.project_id,
                source_id,
                required_excerpt_sha256,
                required_excerpt_sha256,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(
                "project-scoped immutable legal citation candidate not found"
            )
        return tuple(row)

    def _relation_for_review(
        self,
        cursor: Any,
        source_id: str,
        *,
        required_excerpt_sha256: str | None,
    ) -> tuple[Any, ...]:
        cursor.execute(
            """
            WITH current_snapshot AS (
                SELECT snapshot.*
                FROM legal_evidence_snapshots AS snapshot
                WHERE snapshot.tenant_id = %s
                  AND snapshot.project_id = %s
                  AND snapshot.created_at <= statement_timestamp()
                  AND snapshot.as_of_date <= CURRENT_DATE
                  AND snapshot.is_stale = FALSE
                  AND snapshot.freshness_policy_version =
                      'legal-freshness-v1'
                  AND snapshot.citation_verification_status IN (
                      'partial', 'verified'
                  )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM legal_evidence_snapshots AS newer
                        WHERE newer.tenant_id = snapshot.tenant_id
                          AND newer.project_id = snapshot.project_id
                          AND (
                                newer.snapshot_version >
                                    snapshot.snapshot_version
                                OR newer.supersedes_snapshot_id =
                                    snapshot.legal_evidence_snapshot_id
                          )
                  )
                ORDER BY snapshot.snapshot_version DESC,
                         snapshot.created_at DESC,
                         snapshot.legal_evidence_snapshot_id DESC
                LIMIT 1
            )
            SELECT
                relation.subject_resource_id,
                relation.object_resource_id,
                relation.relation_type,
                relation.source_version_id,
                relation.source_provision_id,
                relation.assertion_method,
                relation.algorithm_version,
                relation.confidence,
                relation.as_of_date,
                relation.asserted_at,
                relation.human_review_status,
                relation.metadata_json,
                snapshot.legal_evidence_snapshot_id,
                snapshot.snapshot_version,
                snapshot.snapshot_sha256,
                snapshot.as_of_date,
                item.legal_evidence_item_id,
                item.legal_resource_version_id,
                item.legal_provision_id,
                item.exact_locator,
                item.source_fetch_id,
                provider.provider_id,
                item.excerpt_sha256,
                provision.provision_sha256,
                provision.body_text,
                item.verified_at,
                provider.source_authority,
                provider.authority_tier,
                snapshot.freshness_policy_version
            FROM legal_relation_assertions AS relation
            JOIN current_snapshot AS snapshot ON TRUE
            JOIN legal_evidence_snapshot_items AS item
              ON item.tenant_id = snapshot.tenant_id
             AND item.project_id = snapshot.project_id
             AND item.legal_evidence_snapshot_id =
                 snapshot.legal_evidence_snapshot_id
             AND item.legal_resource_id = relation.subject_resource_id
             AND item.legal_resource_version_id =
                 relation.source_version_id
             AND item.legal_provision_id = relation.source_provision_id
            JOIN legal_resource_versions AS version
              ON version.legal_resource_version_id =
                 item.legal_resource_version_id
             AND version.legal_resource_id = item.legal_resource_id
             AND version.source_fetch_id = item.source_fetch_id
            JOIN legal_resources AS resource
              ON resource.legal_resource_id = item.legal_resource_id
             AND resource.provider_id = version.provider_id
            JOIN legal_source_fetches AS source_fetch
              ON source_fetch.source_fetch_id = item.source_fetch_id
             AND source_fetch.provider_id = version.provider_id
            JOIN legal_source_providers AS provider
              ON provider.provider_id = version.provider_id
            JOIN legal_resource_version_lineages AS lineage
              ON lineage.legal_resource_version_id =
                 version.legal_resource_version_id
             AND lineage.legal_resource_id = version.legal_resource_id
            JOIN legal_provisions AS provision
              ON provision.legal_provision_id = item.legal_provision_id
             AND provision.legal_resource_version_id =
                 item.legal_resource_version_id
             AND provision.legal_resource_id = item.legal_resource_id
            WHERE relation.legal_relation_assertion_id = %s
              AND relation.assertion_status IN ('unknown', 'candidate')
              AND NULLIF(
                    btrim(relation.metadata_json ->> 'reviewStatus'),
                    ''
              ) IS NULL
              AND relation.source_provision_id IS NOT NULL
              AND item.citation_verification_status = 'verified'
              AND item.verified_at IS NOT NULL
              AND item.verified_at <= statement_timestamp()
              AND item.verified_at + INTERVAL '7 days' >=
                  statement_timestamp()
              AND version.verification_status IN (
                    'source_verified', 'schema_verified'
              )
              AND version.verified_at IS NOT NULL
              AND version.verified_at <= statement_timestamp()
              AND source_fetch.fetch_status IN ('succeeded', 'not_modified')
              AND source_fetch.fetched_at <= statement_timestamp()
              AND provider.provider_status = 'active'
              AND lineage.validated_at <= statement_timestamp()
              AND provision.source_locator = item.exact_locator
              AND provision.provision_sha256 =
                  encode(
                      extensions.digest(
                          convert_to(provision.body_text, 'UTF8'),
                          'sha256'
                      ),
                      'hex'
                  )
              AND (
                    version.effective_from IS NULL
                    OR version.effective_from <= snapshot.as_of_date
              )
              AND (
                    version.effective_to IS NULL
                    OR version.effective_to >= snapshot.as_of_date
              )
              AND (
                    version.valid_time_start IS NULL
                    OR version.valid_time_start <= snapshot.as_of_date
              )
              AND (
                    version.valid_time_end IS NULL
                    OR version.valid_time_end >= snapshot.as_of_date
              )
              AND (%s IS NULL OR item.excerpt_sha256 = %s)
              AND NOT EXISTS (
                    SELECT 1
                    FROM legal_resource_versions AS replacement
                    JOIN legal_source_fetches AS replacement_fetch
                      ON replacement_fetch.source_fetch_id =
                         replacement.source_fetch_id
                     AND replacement_fetch.provider_id =
                         replacement.provider_id
                    JOIN legal_source_providers AS replacement_provider
                      ON replacement_provider.provider_id =
                         replacement.provider_id
                    WHERE replacement.legal_resource_id =
                          version.legal_resource_id
                      AND replacement.version_sequence >
                          version.version_sequence
                      AND replacement.verification_status IN (
                          'source_verified', 'schema_verified'
                      )
                      AND replacement.verified_at IS NOT NULL
                      AND replacement.verified_at <= statement_timestamp()
                      AND replacement_fetch.fetch_status IN (
                          'succeeded', 'not_modified'
                      )
                      AND replacement_provider.provider_status = 'active'
                      AND (
                          replacement.supersedes_version_id =
                              version.legal_resource_version_id
                          OR (
                              (
                                  replacement.effective_from IS NULL
                                  OR replacement.effective_from <=
                                     snapshot.as_of_date
                              )
                              AND (
                                  replacement.effective_to IS NULL
                                  OR replacement.effective_to >=
                                     snapshot.as_of_date
                              )
                              AND (
                                  replacement.valid_time_start IS NULL
                                  OR replacement.valid_time_start <=
                                     snapshot.as_of_date
                              )
                              AND (
                                  replacement.valid_time_end IS NULL
                                  OR replacement.valid_time_end >=
                                     snapshot.as_of_date
                              )
                          )
                      )
              )
            ORDER BY item.verified_at DESC,
                     item.legal_evidence_item_id ASC
            LIMIT 1
            """,
            (
                self.tenant_id,
                self.project_id,
                source_id,
                required_excerpt_sha256,
                required_excerpt_sha256,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(
                "project-scoped immutable legal relation candidate not found"
            )
        return tuple(row)

    def _object_binding_for_review(
        self,
        cursor: Any,
        *,
        snapshot_id: str,
        object_resource_id: str,
        evidence_item_id: str,
    ) -> _EvidenceBinding:
        cursor.execute(
            """
            SELECT
                snapshot.legal_evidence_snapshot_id,
                snapshot.snapshot_version,
                snapshot.snapshot_sha256,
                snapshot.as_of_date,
                item.legal_evidence_item_id,
                item.legal_resource_version_id,
                item.legal_provision_id,
                item.exact_locator,
                item.source_fetch_id,
                provider.provider_id,
                item.excerpt_sha256,
                provision.provision_sha256,
                provision.body_text,
                item.verified_at,
                provider.source_authority,
                provider.authority_tier,
                snapshot.freshness_policy_version
            FROM legal_evidence_snapshots AS snapshot
            JOIN legal_evidence_snapshot_items AS item
              ON item.tenant_id = snapshot.tenant_id
             AND item.project_id = snapshot.project_id
             AND item.legal_evidence_snapshot_id =
                 snapshot.legal_evidence_snapshot_id
             AND item.legal_evidence_item_id = %s
             AND item.legal_resource_id = %s
            JOIN legal_resource_versions AS version
              ON version.legal_resource_version_id =
                 item.legal_resource_version_id
             AND version.legal_resource_id = item.legal_resource_id
             AND version.source_fetch_id = item.source_fetch_id
            JOIN legal_resources AS resource
              ON resource.legal_resource_id = item.legal_resource_id
             AND resource.provider_id = version.provider_id
            JOIN legal_source_fetches AS source_fetch
              ON source_fetch.source_fetch_id = item.source_fetch_id
             AND source_fetch.provider_id = version.provider_id
            JOIN legal_source_providers AS provider
              ON provider.provider_id = version.provider_id
            JOIN legal_resource_version_lineages AS lineage
              ON lineage.legal_resource_version_id =
                 version.legal_resource_version_id
             AND lineage.legal_resource_id = version.legal_resource_id
            JOIN legal_provisions AS provision
              ON provision.legal_provision_id = item.legal_provision_id
             AND provision.legal_resource_version_id =
                 item.legal_resource_version_id
             AND provision.legal_resource_id = item.legal_resource_id
            WHERE snapshot.legal_evidence_snapshot_id = %s
              AND snapshot.tenant_id = %s
              AND snapshot.project_id = %s
              AND snapshot.created_at <= statement_timestamp()
              AND snapshot.as_of_date <= CURRENT_DATE
              AND snapshot.is_stale = FALSE
              AND snapshot.freshness_policy_version =
                  'legal-freshness-v1'
              AND snapshot.citation_verification_status IN (
                  'partial', 'verified'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM legal_evidence_snapshots AS newer
                  WHERE newer.tenant_id = snapshot.tenant_id
                    AND newer.project_id = snapshot.project_id
                    AND (
                        newer.snapshot_version > snapshot.snapshot_version
                        OR newer.supersedes_snapshot_id =
                            snapshot.legal_evidence_snapshot_id
                    )
              )
              AND item.citation_verification_status = 'verified'
              AND item.verified_at IS NOT NULL
              AND item.verified_at <= statement_timestamp()
              AND item.verified_at + INTERVAL '7 days' >=
                  statement_timestamp()
              AND version.verification_status IN (
                  'source_verified', 'schema_verified'
              )
              AND version.verified_at IS NOT NULL
              AND version.verified_at <= statement_timestamp()
              AND source_fetch.fetch_status IN (
                  'succeeded', 'not_modified'
              )
              AND source_fetch.fetched_at <= statement_timestamp()
              AND provider.provider_status = 'active'
              AND lineage.validated_at <= statement_timestamp()
              AND provision.source_locator = item.exact_locator
              AND provision.provision_sha256 =
                  encode(
                      extensions.digest(
                          convert_to(provision.body_text, 'UTF8'),
                          'sha256'
                      ),
                      'hex'
                  )
              AND (
                  version.effective_from IS NULL
                  OR version.effective_from <= snapshot.as_of_date
              )
              AND (
                  version.effective_to IS NULL
                  OR version.effective_to >= snapshot.as_of_date
              )
              AND (
                  version.valid_time_start IS NULL
                  OR version.valid_time_start <= snapshot.as_of_date
              )
              AND (
                  version.valid_time_end IS NULL
                  OR version.valid_time_end >= snapshot.as_of_date
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM legal_resource_versions AS replacement
                  JOIN legal_source_fetches AS replacement_fetch
                    ON replacement_fetch.source_fetch_id =
                       replacement.source_fetch_id
                   AND replacement_fetch.provider_id =
                       replacement.provider_id
                  JOIN legal_source_providers AS replacement_provider
                    ON replacement_provider.provider_id =
                       replacement.provider_id
                  WHERE replacement.legal_resource_id =
                        version.legal_resource_id
                    AND replacement.version_sequence >
                        version.version_sequence
                    AND replacement.verification_status IN (
                        'source_verified', 'schema_verified'
                    )
                    AND replacement.verified_at IS NOT NULL
                    AND replacement.verified_at <= statement_timestamp()
                    AND replacement_fetch.fetch_status IN (
                        'succeeded', 'not_modified'
                    )
                    AND replacement_provider.provider_status = 'active'
                    AND (
                        replacement.supersedes_version_id =
                            version.legal_resource_version_id
                        OR (
                            (
                                replacement.effective_from IS NULL
                                OR replacement.effective_from <=
                                   snapshot.as_of_date
                            )
                            AND (
                                replacement.effective_to IS NULL
                                OR replacement.effective_to >=
                                   snapshot.as_of_date
                            )
                            AND (
                                replacement.valid_time_start IS NULL
                                OR replacement.valid_time_start <=
                                   snapshot.as_of_date
                            )
                            AND (
                                replacement.valid_time_end IS NULL
                                OR replacement.valid_time_end >=
                                   snapshot.as_of_date
                            )
                        )
                    )
              )
            """,
            (
                evidence_item_id,
                object_resource_id,
                snapshot_id,
                self.tenant_id,
                self.project_id,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(
                "relation object evidence item is not an exact current "
                "binding in the selected project snapshot"
            )
        return _EvidenceBinding.from_row(tuple(row), offset=0)

    def _latest_citation_review(
        self,
        cursor: Any,
        source_id: str,
    ) -> tuple[Any, ...] | None:
        cursor.execute(
            """
            SELECT
                review.project_legal_citation_review_version_id,
                review.review_version,
                event.project_legal_citation_review_event_id
            FROM project_legal_citation_review_versions AS review
            LEFT JOIN project_legal_citation_review_events AS event
              ON event.project_legal_citation_review_version_id =
                 review.project_legal_citation_review_version_id
             AND event.tenant_id = review.tenant_id
             AND event.project_id = review.project_id
            WHERE review.tenant_id = %s
              AND review.project_id = %s
              AND review.legal_citation_id = %s
            ORDER BY review.review_version DESC
            LIMIT 1
            FOR UPDATE OF review
            """,
            (self.tenant_id, self.project_id, source_id),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None

    def _latest_relation_review(
        self,
        cursor: Any,
        source_id: str,
    ) -> tuple[Any, ...] | None:
        cursor.execute(
            """
            SELECT
                review.project_legal_relation_review_version_id,
                review.review_version,
                event.project_legal_relation_review_event_id
            FROM project_legal_relation_review_versions AS review
            LEFT JOIN project_legal_relation_review_events AS event
              ON event.project_legal_relation_review_version_id =
                 review.project_legal_relation_review_version_id
             AND event.tenant_id = review.tenant_id
             AND event.project_id = review.project_id
            WHERE review.tenant_id = %s
              AND review.project_id = %s
              AND review.legal_relation_assertion_id = %s
            ORDER BY review.review_version DESC
            LIMIT 1
            FOR UPDATE OF review
            """,
            (self.tenant_id, self.project_id, source_id),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None

    @staticmethod
    def _review_chain(
        latest: tuple[Any, ...] | None,
        requested_event_id: str | None,
    ) -> tuple[int, str | None, str | None]:
        requested = str(requested_event_id or "").strip() or None
        if latest is None:
            if requested is not None:
                raise ValueError(
                    "supersedesReviewEventId must be absent for the first "
                    "project Citator review"
                )
            return 1, None, None
        predecessor_id = str(latest[0] or "").strip()
        predecessor_version = int(latest[1])
        predecessor_event_id = str(latest[2] or "").strip() or None
        if predecessor_event_id is None:
            raise ValueError(
                "latest project Citator review version has no append-only event"
            )
        if requested != predecessor_event_id:
            raise ValueError(
                "supersedesReviewEventId must exactly identify the project's "
                "latest review event"
            )
        return (
            predecessor_version + 1,
            predecessor_id,
            predecessor_event_id,
        )

    def _list_citations(self, cursor: Any, limit: int) -> list[dict[str, Any]]:
        cursor.execute(
            """
            SELECT
                citation.legal_citation_id,
                citation.cited_identifier_text,
                CASE
                    WHEN latest.review_version IS NOT NULL
                     AND latest.legal_evidence_snapshot_id =
                         binding.legal_evidence_snapshot_id
                     AND latest.legal_evidence_item_id =
                         binding.legal_evidence_item_id
                     AND latest.legal_resource_version_id =
                         binding.legal_resource_version_id
                     AND latest.legal_provision_id =
                         binding.legal_provision_id
                     AND latest.source_fetch_id = binding.source_fetch_id
                     AND latest.provider_id = binding.provider_id
                     AND latest.evidence_exact_locator =
                         binding.exact_locator
                     AND latest.bound_excerpt_sha256 =
                         binding.excerpt_sha256
                     AND latest.bound_provision_sha256 =
                         binding.provision_sha256
                    THEN latest.effective_citation_status
                    ELSE citation.citation_status
                END,
                citation.assertion_method,
                citation.confidence,
                citation.asserted_at,
                CASE
                    WHEN latest.review_version IS NOT NULL
                     AND latest.legal_evidence_snapshot_id =
                         binding.legal_evidence_snapshot_id
                     AND latest.legal_evidence_item_id =
                         binding.legal_evidence_item_id
                     AND latest.legal_resource_version_id =
                         binding.legal_resource_version_id
                     AND latest.legal_provision_id =
                         binding.legal_provision_id
                     AND latest.source_fetch_id = binding.source_fetch_id
                     AND latest.provider_id = binding.provider_id
                     AND latest.evidence_exact_locator =
                         binding.exact_locator
                     AND latest.bound_excerpt_sha256 =
                         binding.excerpt_sha256
                     AND latest.bound_provision_sha256 =
                         binding.provision_sha256
                    THEN latest.human_review_status
                    ELSE citation.human_review_status
                END,
                latest.reviewer_id,
                latest.reviewed_at,
                latest.project_legal_citation_review_event_id,
                latest.project_legal_citation_review_version_id,
                latest.review_version,
                latest.review_status,
                citation.citation_status,
                binding.legal_evidence_snapshot_id,
                binding.snapshot_version,
                binding.snapshot_sha256,
                binding.as_of_date,
                binding.legal_evidence_item_id,
                binding.legal_resource_version_id,
                binding.legal_provision_id,
                binding.exact_locator,
                binding.source_fetch_id,
                binding.provider_id,
                binding.excerpt_sha256,
                binding.provision_sha256,
                binding.verified_at,
                binding.source_authority,
                binding.authority_tier,
                binding.freshness_policy_version,
                CASE
                    WHEN latest.review_version IS NULL THEN NULL
                    ELSE (
                        latest.legal_evidence_snapshot_id =
                            binding.legal_evidence_snapshot_id
                        AND latest.legal_evidence_item_id =
                            binding.legal_evidence_item_id
                        AND latest.legal_resource_version_id =
                            binding.legal_resource_version_id
                        AND latest.legal_provision_id =
                            binding.legal_provision_id
                        AND latest.source_fetch_id = binding.source_fetch_id
                        AND latest.provider_id = binding.provider_id
                        AND latest.evidence_exact_locator =
                            binding.exact_locator
                        AND latest.bound_excerpt_sha256 =
                            binding.excerpt_sha256
                        AND latest.bound_provision_sha256 =
                            binding.provision_sha256
                    )
                END
            FROM legal_citations AS citation
            JOIN LATERAL (
                SELECT
                    snapshot.legal_evidence_snapshot_id,
                    snapshot.snapshot_version,
                    snapshot.snapshot_sha256,
                    snapshot.as_of_date,
                    item.legal_evidence_item_id,
                    item.legal_resource_version_id,
                    item.legal_provision_id,
                    item.exact_locator,
                    item.source_fetch_id,
                    provider.provider_id,
                    item.excerpt_sha256,
                    provision.provision_sha256,
                    item.verified_at,
                    provider.source_authority,
                    provider.authority_tier,
                    snapshot.freshness_policy_version
                FROM legal_evidence_snapshots AS snapshot
                JOIN legal_evidence_snapshot_items AS item
                  ON item.tenant_id = snapshot.tenant_id
                 AND item.project_id = snapshot.project_id
                 AND item.legal_evidence_snapshot_id =
                     snapshot.legal_evidence_snapshot_id
                 AND item.legal_resource_id =
                     citation.source_resource_id
                 AND item.legal_resource_version_id =
                     citation.source_version_id
                 AND item.legal_provision_id =
                     citation.source_provision_id
                JOIN legal_resource_versions AS version
                  ON version.legal_resource_version_id =
                     item.legal_resource_version_id
                 AND version.legal_resource_id = item.legal_resource_id
                 AND version.source_fetch_id = item.source_fetch_id
                JOIN legal_resources AS resource
                  ON resource.legal_resource_id = item.legal_resource_id
                 AND resource.provider_id = version.provider_id
                JOIN legal_source_fetches AS source_fetch
                  ON source_fetch.source_fetch_id = item.source_fetch_id
                 AND source_fetch.provider_id = version.provider_id
                JOIN legal_source_providers AS provider
                  ON provider.provider_id = version.provider_id
                JOIN legal_resource_version_lineages AS lineage
                  ON lineage.legal_resource_version_id =
                     version.legal_resource_version_id
                 AND lineage.legal_resource_id = version.legal_resource_id
                JOIN legal_provisions AS provision
                  ON provision.legal_provision_id =
                     item.legal_provision_id
                 AND provision.legal_resource_version_id =
                     item.legal_resource_version_id
                 AND provision.legal_resource_id = item.legal_resource_id
                WHERE snapshot.tenant_id = %s
                  AND snapshot.project_id = %s
                  AND snapshot.created_at <= statement_timestamp()
                  AND snapshot.as_of_date <= CURRENT_DATE
                  AND snapshot.is_stale = FALSE
                  AND snapshot.freshness_policy_version =
                      'legal-freshness-v1'
                  AND snapshot.citation_verification_status IN (
                      'partial', 'verified'
                  )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM legal_evidence_snapshots AS newer
                        WHERE newer.tenant_id = snapshot.tenant_id
                          AND newer.project_id = snapshot.project_id
                          AND (
                                newer.snapshot_version >
                                    snapshot.snapshot_version
                                OR newer.supersedes_snapshot_id =
                                    snapshot.legal_evidence_snapshot_id
                          )
                  )
                  AND item.citation_verification_status = 'verified'
                  AND item.verified_at IS NOT NULL
                  AND item.verified_at <= statement_timestamp()
                  AND item.verified_at + INTERVAL '7 days' >=
                      statement_timestamp()
                  AND version.verification_status IN (
                      'source_verified', 'schema_verified'
                  )
                  AND version.verified_at IS NOT NULL
                  AND version.verified_at <= statement_timestamp()
                  AND source_fetch.fetch_status IN (
                      'succeeded', 'not_modified'
                  )
                  AND source_fetch.fetched_at <= statement_timestamp()
                  AND provider.provider_status = 'active'
                  AND lineage.validated_at <= statement_timestamp()
                  AND provision.source_locator = item.exact_locator
                  AND provision.provision_sha256 =
                      encode(
                          extensions.digest(
                              convert_to(provision.body_text, 'UTF8'),
                              'sha256'
                          ),
                          'hex'
                      )
                  AND (
                        version.effective_from IS NULL
                        OR version.effective_from <= snapshot.as_of_date
                  )
                  AND (
                        version.effective_to IS NULL
                        OR version.effective_to >= snapshot.as_of_date
                  )
                  AND (
                        version.valid_time_start IS NULL
                        OR version.valid_time_start <= snapshot.as_of_date
                  )
                  AND (
                        version.valid_time_end IS NULL
                        OR version.valid_time_end >= snapshot.as_of_date
                  )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM legal_resource_versions AS replacement
                        JOIN legal_source_fetches AS replacement_fetch
                          ON replacement_fetch.source_fetch_id =
                             replacement.source_fetch_id
                         AND replacement_fetch.provider_id =
                             replacement.provider_id
                        JOIN legal_source_providers AS replacement_provider
                          ON replacement_provider.provider_id =
                             replacement.provider_id
                        WHERE replacement.legal_resource_id =
                              version.legal_resource_id
                          AND replacement.version_sequence >
                              version.version_sequence
                          AND replacement.verification_status IN (
                              'source_verified', 'schema_verified'
                          )
                          AND replacement.verified_at IS NOT NULL
                          AND replacement.verified_at <=
                              statement_timestamp()
                          AND replacement_fetch.fetch_status IN (
                              'succeeded', 'not_modified'
                          )
                          AND replacement_provider.provider_status = 'active'
                          AND (
                                replacement.supersedes_version_id =
                                    version.legal_resource_version_id
                                OR (
                                    (
                                        replacement.effective_from IS NULL
                                        OR replacement.effective_from <=
                                           snapshot.as_of_date
                                    )
                                    AND (
                                        replacement.effective_to IS NULL
                                        OR replacement.effective_to >=
                                           snapshot.as_of_date
                                    )
                                    AND (
                                        replacement.valid_time_start IS NULL
                                        OR replacement.valid_time_start <=
                                           snapshot.as_of_date
                                    )
                                    AND (
                                        replacement.valid_time_end IS NULL
                                        OR replacement.valid_time_end >=
                                           snapshot.as_of_date
                                    )
                                )
                          )
                  )
                ORDER BY snapshot.snapshot_version DESC,
                         snapshot.created_at DESC,
                         item.verified_at DESC,
                         item.legal_evidence_item_id ASC
                LIMIT 1
            ) AS binding ON TRUE
            LEFT JOIN LATERAL (
                SELECT
                    review.project_legal_citation_review_version_id,
                    review.review_version,
                    review.review_status,
                    review.effective_citation_status,
                    review.human_review_status,
                    review.reviewer_id,
                    review.reviewed_at,
                    review.legal_evidence_snapshot_id,
                    review.legal_evidence_item_id,
                    review.legal_resource_version_id,
                    review.legal_provision_id,
                    review.source_fetch_id,
                    review.provider_id,
                    review.evidence_exact_locator,
                    review.bound_excerpt_sha256,
                    review.bound_provision_sha256,
                    event.project_legal_citation_review_event_id
                FROM project_legal_citation_review_versions AS review
                LEFT JOIN project_legal_citation_review_events AS event
                  ON event.project_legal_citation_review_version_id =
                     review.project_legal_citation_review_version_id
                 AND event.tenant_id = review.tenant_id
                 AND event.project_id = review.project_id
                WHERE review.tenant_id = %s
                  AND review.project_id = %s
                  AND review.legal_citation_id =
                      citation.legal_citation_id
                ORDER BY review.review_version DESC
                LIMIT 1
            ) AS latest ON TRUE
            WHERE citation.citation_status IN (
                    'unknown', 'unresolved', 'ambiguous', 'resolved'
              )
              AND NULLIF(
                    btrim(citation.metadata_json ->> 'reviewStatus'),
                    ''
              ) IS NULL
              AND citation.source_provision_id IS NOT NULL
            ORDER BY citation.asserted_at ASC,
                     citation.legal_citation_id ASC
            LIMIT %s
            """,
            (
                self.tenant_id,
                self.project_id,
                self.tenant_id,
                self.project_id,
                limit,
            ),
        )
        return [
            {
                "recordKind": "citation",
                "recordId": row[0],
                "identifier": row[1],
                "status": row[2],
                "assertionMethod": row[3],
                "confidence": row[4],
                "assertedAt": row[5],
                "humanReviewStatus": row[6],
                "reviewedBy": row[7],
                "reviewedAt": row[8],
                "latestReviewEventId": row[9],
                "latestReviewVersionId": row[10],
                "reviewVersion": row[11],
                "reviewStatus": row[12],
                "corpusStatus": row[13],
                "currentEvidenceBinding": self._queue_evidence_binding(
                    row,
                    14,
                ),
                "reviewBindingCurrent": row[30],
            }
            for row in cursor.fetchall()
        ]

    def _list_relations(self, cursor: Any, limit: int) -> list[dict[str, Any]]:
        cursor.execute(
            """
            SELECT
                relation.legal_relation_assertion_id,
                relation.subject_resource_id,
                relation.object_resource_id,
                relation.relation_type,
                CASE
                    WHEN latest.review_version IS NOT NULL
                     AND latest.legal_evidence_snapshot_id =
                         binding.legal_evidence_snapshot_id
                     AND latest.legal_evidence_item_id =
                         binding.legal_evidence_item_id
                     AND latest.legal_resource_version_id =
                         binding.legal_resource_version_id
                     AND latest.legal_provision_id =
                         binding.legal_provision_id
                     AND latest.source_fetch_id = binding.source_fetch_id
                     AND latest.provider_id = binding.provider_id
                     AND latest.evidence_exact_locator =
                         binding.exact_locator
                     AND latest.bound_excerpt_sha256 =
                         binding.excerpt_sha256
                     AND latest.bound_provision_sha256 =
                         binding.provision_sha256
                    THEN latest.effective_assertion_status
                    ELSE relation.assertion_status
                END,
                relation.assertion_method,
                relation.confidence,
                relation.asserted_at,
                CASE
                    WHEN latest.review_version IS NOT NULL
                     AND latest.legal_evidence_snapshot_id =
                         binding.legal_evidence_snapshot_id
                     AND latest.legal_evidence_item_id =
                         binding.legal_evidence_item_id
                     AND latest.legal_resource_version_id =
                         binding.legal_resource_version_id
                     AND latest.legal_provision_id =
                         binding.legal_provision_id
                     AND latest.source_fetch_id = binding.source_fetch_id
                     AND latest.provider_id = binding.provider_id
                     AND latest.evidence_exact_locator =
                         binding.exact_locator
                     AND latest.bound_excerpt_sha256 =
                         binding.excerpt_sha256
                     AND latest.bound_provision_sha256 =
                         binding.provision_sha256
                    THEN latest.human_review_status
                    ELSE relation.human_review_status
                END,
                latest.reviewer_id,
                latest.reviewed_at,
                latest.project_legal_relation_review_event_id,
                latest.project_legal_relation_review_version_id,
                latest.review_version,
                latest.review_status,
                relation.assertion_status,
                binding.legal_evidence_snapshot_id,
                binding.snapshot_version,
                binding.snapshot_sha256,
                binding.as_of_date,
                binding.legal_evidence_item_id,
                binding.legal_resource_version_id,
                binding.legal_provision_id,
                binding.exact_locator,
                binding.source_fetch_id,
                binding.provider_id,
                binding.excerpt_sha256,
                binding.provision_sha256,
                binding.verified_at,
                binding.source_authority,
                binding.authority_tier,
                binding.freshness_policy_version,
                CASE
                    WHEN latest.review_version IS NULL THEN NULL
                    ELSE (
                        latest.legal_evidence_snapshot_id =
                            binding.legal_evidence_snapshot_id
                        AND latest.legal_evidence_item_id =
                            binding.legal_evidence_item_id
                        AND latest.legal_resource_version_id =
                            binding.legal_resource_version_id
                        AND latest.legal_provision_id =
                            binding.legal_provision_id
                        AND latest.source_fetch_id = binding.source_fetch_id
                        AND latest.provider_id = binding.provider_id
                        AND latest.evidence_exact_locator =
                            binding.exact_locator
                        AND latest.bound_excerpt_sha256 =
                            binding.excerpt_sha256
                        AND latest.bound_provision_sha256 =
                            binding.provision_sha256
                    )
                END
            FROM legal_relation_assertions AS relation
            JOIN LATERAL (
                SELECT
                    snapshot.legal_evidence_snapshot_id,
                    snapshot.snapshot_version,
                    snapshot.snapshot_sha256,
                    snapshot.as_of_date,
                    item.legal_evidence_item_id,
                    item.legal_resource_version_id,
                    item.legal_provision_id,
                    item.exact_locator,
                    item.source_fetch_id,
                    provider.provider_id,
                    item.excerpt_sha256,
                    provision.provision_sha256,
                    item.verified_at,
                    provider.source_authority,
                    provider.authority_tier,
                    snapshot.freshness_policy_version
                FROM legal_evidence_snapshots AS snapshot
                JOIN legal_evidence_snapshot_items AS item
                  ON item.tenant_id = snapshot.tenant_id
                 AND item.project_id = snapshot.project_id
                 AND item.legal_evidence_snapshot_id =
                     snapshot.legal_evidence_snapshot_id
                 AND item.legal_resource_id =
                     relation.subject_resource_id
                 AND item.legal_resource_version_id =
                     relation.source_version_id
                 AND item.legal_provision_id =
                     relation.source_provision_id
                JOIN legal_resource_versions AS version
                  ON version.legal_resource_version_id =
                     item.legal_resource_version_id
                 AND version.legal_resource_id = item.legal_resource_id
                 AND version.source_fetch_id = item.source_fetch_id
                JOIN legal_resources AS resource
                  ON resource.legal_resource_id = item.legal_resource_id
                 AND resource.provider_id = version.provider_id
                JOIN legal_source_fetches AS source_fetch
                  ON source_fetch.source_fetch_id = item.source_fetch_id
                 AND source_fetch.provider_id = version.provider_id
                JOIN legal_source_providers AS provider
                  ON provider.provider_id = version.provider_id
                JOIN legal_resource_version_lineages AS lineage
                  ON lineage.legal_resource_version_id =
                     version.legal_resource_version_id
                 AND lineage.legal_resource_id = version.legal_resource_id
                JOIN legal_provisions AS provision
                  ON provision.legal_provision_id =
                     item.legal_provision_id
                 AND provision.legal_resource_version_id =
                     item.legal_resource_version_id
                 AND provision.legal_resource_id = item.legal_resource_id
                WHERE snapshot.tenant_id = %s
                  AND snapshot.project_id = %s
                  AND snapshot.created_at <= statement_timestamp()
                  AND snapshot.as_of_date <= CURRENT_DATE
                  AND snapshot.is_stale = FALSE
                  AND snapshot.freshness_policy_version =
                      'legal-freshness-v1'
                  AND snapshot.citation_verification_status IN (
                      'partial', 'verified'
                  )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM legal_evidence_snapshots AS newer
                        WHERE newer.tenant_id = snapshot.tenant_id
                          AND newer.project_id = snapshot.project_id
                          AND (
                                newer.snapshot_version >
                                    snapshot.snapshot_version
                                OR newer.supersedes_snapshot_id =
                                    snapshot.legal_evidence_snapshot_id
                          )
                  )
                  AND item.citation_verification_status = 'verified'
                  AND item.verified_at IS NOT NULL
                  AND item.verified_at <= statement_timestamp()
                  AND item.verified_at + INTERVAL '7 days' >=
                      statement_timestamp()
                  AND version.verification_status IN (
                      'source_verified', 'schema_verified'
                  )
                  AND version.verified_at IS NOT NULL
                  AND version.verified_at <= statement_timestamp()
                  AND source_fetch.fetch_status IN (
                      'succeeded', 'not_modified'
                  )
                  AND source_fetch.fetched_at <= statement_timestamp()
                  AND provider.provider_status = 'active'
                  AND lineage.validated_at <= statement_timestamp()
                  AND provision.source_locator = item.exact_locator
                  AND provision.provision_sha256 =
                      encode(
                          extensions.digest(
                              convert_to(provision.body_text, 'UTF8'),
                              'sha256'
                          ),
                          'hex'
                      )
                  AND (
                        version.effective_from IS NULL
                        OR version.effective_from <= snapshot.as_of_date
                  )
                  AND (
                        version.effective_to IS NULL
                        OR version.effective_to >= snapshot.as_of_date
                  )
                  AND (
                        version.valid_time_start IS NULL
                        OR version.valid_time_start <= snapshot.as_of_date
                  )
                  AND (
                        version.valid_time_end IS NULL
                        OR version.valid_time_end >= snapshot.as_of_date
                  )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM legal_resource_versions AS replacement
                        JOIN legal_source_fetches AS replacement_fetch
                          ON replacement_fetch.source_fetch_id =
                             replacement.source_fetch_id
                         AND replacement_fetch.provider_id =
                             replacement.provider_id
                        JOIN legal_source_providers AS replacement_provider
                          ON replacement_provider.provider_id =
                             replacement.provider_id
                        WHERE replacement.legal_resource_id =
                              version.legal_resource_id
                          AND replacement.version_sequence >
                              version.version_sequence
                          AND replacement.verification_status IN (
                              'source_verified', 'schema_verified'
                          )
                          AND replacement.verified_at IS NOT NULL
                          AND replacement.verified_at <=
                              statement_timestamp()
                          AND replacement_fetch.fetch_status IN (
                              'succeeded', 'not_modified'
                          )
                          AND replacement_provider.provider_status = 'active'
                          AND (
                                replacement.supersedes_version_id =
                                    version.legal_resource_version_id
                                OR (
                                    (
                                        replacement.effective_from IS NULL
                                        OR replacement.effective_from <=
                                           snapshot.as_of_date
                                    )
                                    AND (
                                        replacement.effective_to IS NULL
                                        OR replacement.effective_to >=
                                           snapshot.as_of_date
                                    )
                                    AND (
                                        replacement.valid_time_start IS NULL
                                        OR replacement.valid_time_start <=
                                           snapshot.as_of_date
                                    )
                                    AND (
                                        replacement.valid_time_end IS NULL
                                        OR replacement.valid_time_end >=
                                           snapshot.as_of_date
                                    )
                                )
                          )
                  )
                ORDER BY snapshot.snapshot_version DESC,
                         snapshot.created_at DESC,
                         item.verified_at DESC,
                         item.legal_evidence_item_id ASC
                LIMIT 1
            ) AS binding ON TRUE
            LEFT JOIN LATERAL (
                SELECT
                    review.project_legal_relation_review_version_id,
                    review.review_version,
                    review.review_status,
                    review.effective_assertion_status,
                    review.human_review_status,
                    review.reviewer_id,
                    review.reviewed_at,
                    review.legal_evidence_snapshot_id,
                    review.legal_evidence_item_id,
                    review.legal_resource_version_id,
                    review.legal_provision_id,
                    review.source_fetch_id,
                    review.provider_id,
                    review.evidence_exact_locator,
                    review.bound_excerpt_sha256,
                    review.bound_provision_sha256,
                    event.project_legal_relation_review_event_id
                FROM project_legal_relation_review_versions AS review
                LEFT JOIN project_legal_relation_review_events AS event
                  ON event.project_legal_relation_review_version_id =
                     review.project_legal_relation_review_version_id
                 AND event.tenant_id = review.tenant_id
                 AND event.project_id = review.project_id
                WHERE review.tenant_id = %s
                  AND review.project_id = %s
                  AND review.legal_relation_assertion_id =
                      relation.legal_relation_assertion_id
                ORDER BY review.review_version DESC
                LIMIT 1
            ) AS latest ON TRUE
            WHERE relation.assertion_status IN ('unknown', 'candidate')
              AND NULLIF(
                    btrim(relation.metadata_json ->> 'reviewStatus'),
                    ''
              ) IS NULL
              AND relation.source_provision_id IS NOT NULL
            ORDER BY relation.asserted_at ASC,
                     relation.legal_relation_assertion_id ASC
            LIMIT %s
            """,
            (
                self.tenant_id,
                self.project_id,
                self.tenant_id,
                self.project_id,
                limit,
            ),
        )
        return [
            {
                "recordKind": "relation",
                "recordId": row[0],
                "subjectResourceId": row[1],
                "objectResourceId": row[2],
                "relationType": row[3],
                "status": row[4],
                "assertionMethod": row[5],
                "confidence": row[6],
                "assertedAt": row[7],
                "humanReviewStatus": row[8],
                "reviewedBy": row[9],
                "reviewedAt": row[10],
                "latestReviewEventId": row[11],
                "latestReviewVersionId": row[12],
                "reviewVersion": row[13],
                "reviewStatus": row[14],
                "corpusStatus": row[15],
                "currentEvidenceBinding": self._queue_evidence_binding(
                    row,
                    16,
                ),
                "reviewBindingCurrent": row[32],
            }
            for row in cursor.fetchall()
        ]

    @staticmethod
    def _queue_evidence_binding(
        row: tuple[Any, ...],
        offset: int,
    ) -> dict[str, Any]:
        """Return only server-derived, non-privileged lineage metadata."""

        return {
            "snapshotId": row[offset],
            "snapshotVersion": row[offset + 1],
            "snapshotSha256": row[offset + 2],
            "snapshotAsOfDate": row[offset + 3],
            "evidenceItemId": row[offset + 4],
            "resourceVersionId": row[offset + 5],
            "provisionId": row[offset + 6],
            "exactLocator": row[offset + 7],
            "sourceFetchId": row[offset + 8],
            "providerId": row[offset + 9],
            "excerptSha256": row[offset + 10],
            "provisionSha256": row[offset + 11],
            "verifiedAt": row[offset + 12],
            "sourceAuthority": row[offset + 13],
            "authorityTier": row[offset + 14],
            "freshnessPolicyVersion": row[offset + 15],
            "current": True,
        }

    def _validate_review_authority(
        self,
        cursor: Any,
        *,
        review: CitatorReviewInput,
        reviewer_id: str,
        reviewer_role: str,
        reviewed_at: datetime,
    ) -> str | None:
        delegation_id = str(
            review.legal_approval_delegation_id or ""
        ).strip() or None
        if reviewer_role == "external_counsel" and delegation_id is None:
            raise PermissionError(
                "external counsel Citator review requires an explicit "
                "legalApprovalDelegationId"
            )
        if delegation_id is None:
            return None
        cursor.execute(
            """
            SELECT delegation.legal_approval_delegation_id
            FROM legal_approval_delegations AS delegation
            WHERE delegation.legal_approval_delegation_id = %s
              AND delegation.tenant_id = %s
              AND delegation.project_id = %s
              AND delegation.grantee_id = %s
              AND delegation.grantee_role = %s
              AND delegation.scope = 'citator_review'
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
                      AND successor.created_at <= %s
              )
            """,
            (
                delegation_id,
                self.tenant_id,
                self.project_id,
                reviewer_id,
                reviewer_role,
                reviewed_at,
                reviewed_at,
                reviewed_at,
                reviewed_at,
            ),
        )
        row = cursor.fetchone()
        if row is None or str(row[0] or "").strip() != delegation_id:
            raise PermissionError(
                "active project-scoped Citator review delegation not found"
            )
        return delegation_id

    @staticmethod
    def _source_maker(
        metadata: Any,
        *,
        assertion_method: Any,
    ) -> str:
        values = dict(metadata) if isinstance(metadata, Mapping) else {}
        for key in _MAKER_KEYS:
            maker = str(values.get(key) or "").strip()
            if maker:
                return maker
        if str(assertion_method or "").strip() == "human_asserted":
            raise ValueError(
                "human-asserted Corpus candidate has no immutable maker id"
            )
        return "official_corpus"

    @staticmethod
    def _assert_maker_checker(maker_id: str, reviewer_id: str) -> None:
        if maker_id.casefold() == reviewer_id.casefold():
            raise PermissionError(
                "Citator maker and checker must be different human actors"
            )

    @staticmethod
    def _reviewer(
        reviewer_id: str,
        reviewer_role: str,
        *,
        final: bool,
    ) -> tuple[str, str]:
        reviewer = str(reviewer_id or "").strip()
        role = str(reviewer_role or "").strip().casefold()
        if (
            not reviewer
            or reviewer.casefold() in {"anonymous", "unknown", "system"}
            or role not in REVIEWER_ROLES
        ):
            raise ValueError(
                "Citator review requires an authenticated human legal reviewer"
            )
        if final and role not in FINAL_COUNSEL_ROLES:
            raise PermissionError(
                "final Citator decision requires legal_counsel_approver "
                "or external_counsel"
            )
        return reviewer, role

    @staticmethod
    def _review_evidence(
        review: CitatorReviewInput,
    ) -> tuple[str | None, str | None]:
        excerpt = str(review.evidence_excerpt or "").strip() or None
        digest = (
            str(review.evidence_excerpt_sha256 or "")
            .casefold()
            .removeprefix("sha256:")
            or None
        )
        if review.review_status == CitatorReviewStatus.APPROVED:
            if excerpt is None or digest is None:
                raise ValueError(
                    "final Citator verification requires exact excerpt and hash"
                )
            if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != digest:
                raise ValueError("evidence excerpt hash mismatch")
        return excerpt, digest

    @staticmethod
    def _validate_approved_binding(
        review: CitatorReviewInput,
        *,
        excerpt: str | None,
        excerpt_hash: str | None,
        binding: _EvidenceBinding,
    ) -> None:
        """Bind final approval to the exact official provision bytes.

        Non-final review states still require a current project evidence
        binding, but may intentionally omit an excerpt while a correction is
        requested.  Approval is stricter: the submitted excerpt must be the
        exact snapshot-item excerpt, must occur verbatim in the immutable
        provision body, and both stored hashes must match the supplied bytes.
        """

        provision_hash = hashlib.sha256(
            binding.provision_body.encode("utf-8")
        ).hexdigest()
        if provision_hash != binding.provision_sha256:
            raise ValueError("bound official provision hash mismatch")
        if review.review_status != CitatorReviewStatus.APPROVED:
            return
        if excerpt is None or excerpt_hash is None:
            raise ValueError(
                "final Citator verification requires exact excerpt and hash"
            )
        if excerpt_hash != binding.excerpt_sha256:
            raise ValueError(
                "approved excerpt hash does not match current evidence item"
            )
        if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != excerpt_hash:
            raise ValueError("evidence excerpt hash mismatch")
        if excerpt not in binding.provision_body:
            raise ValueError(
                "approved excerpt is not an exact substring of the "
                "official provision body"
            )

    @staticmethod
    def _citation_statuses(
        status: CitatorReviewStatus,
    ) -> tuple[str, str]:
        if status == CitatorReviewStatus.APPROVED:
            return "verified", "approved"
        if status == CitatorReviewStatus.REJECTED:
            return "rejected", "rejected"
        return "unknown", "pending"

    @staticmethod
    def _relation_statuses(
        status: CitatorReviewStatus,
    ) -> tuple[str, str]:
        if status == CitatorReviewStatus.APPROVED:
            return "verified", "approved"
        if status == CitatorReviewStatus.REJECTED:
            return "rejected", "rejected"
        return "unknown", "pending"

    def _lock_record(
        self,
        cursor: Any,
        kind: CitatorRecordKind,
        source_id: str,
    ) -> None:
        cursor.execute(
            """
            SELECT pg_advisory_xact_lock(
                pg_catalog.hashtextextended(%s, 0)
            )
            """,
            (
                "project-citator:"
                f"{self.tenant_id}:{self.project_id}:{kind.value}:{source_id}",
            ),
        )

    @staticmethod
    def _record_id(value: str, label: str) -> str:
        result = str(value or "").strip()
        if not result:
            raise ValueError(f"{label} id is required")
        return result

    class _CursorContext:
        def __init__(
            self,
            repository: "PostgresLegalCitatorRepository",
        ) -> None:
            self.repository = repository
            self.connection: Any = None
            self.cursor: Any = None

        def __enter__(self) -> Any:
            self.connection = self.repository._review_connect()
            self.connection.__enter__()
            self.cursor = self.connection.cursor()
            self.cursor.__enter__()
            self.cursor.execute(
                f'SET LOCAL search_path TO "{self.repository._schema}", '
                "pg_catalog, pg_temp"
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

    def _review_cursor(self) -> "_CursorContext":
        return self._CursorContext(self)

    @staticmethod
    def _inserted_id(cursor: Any, label: str) -> str:
        row = cursor.fetchone()
        if row is None or not str(row[0] or "").strip():
            raise ValueError(f"{label} was not inserted")
        return str(row[0])


__all__ = [
    "FINAL_COUNSEL_ROLES",
    "FINAL_REVIEW_STATUSES",
    "PostgresLegalCitatorRepository",
    "REVIEWER_ROLES",
]
