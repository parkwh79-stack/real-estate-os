"""Immutable tenant-native Legal Pod review/permit projection repository."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from db.postgres import connect
from db.postgres_runtime_privileges import validate_database_identifier
from schemas.legal_review_projection import (
    LegalReviewProjectionInput,
    PermitReviewProjectionInput,
)


ConnectionFactory = Callable[[], Any]
ProjectionInput = LegalReviewProjectionInput | PermitReviewProjectionInput
_TABLES = {
    "legal": "project_legal_review_versions",
    "permit": "project_permit_review_versions",
}


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


def _bare_hash(value: str | None) -> str | None:
    text = str(value or "").strip().casefold().removeprefix("sha256:")
    return text or None


class PostgresLegalReviewProjectionRepository:
    """Project-bound, INSERT-only normalized review-run ledger.

    One call appends the Legal and Permit projections in a single transaction.
    RLS is activated with the tenant GUC before any read or insert.  The
    repository never reads the legacy ``legal_reviews``/``permit_reviews``
    stores.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_factory: ConnectionFactory = connect,
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

    def append_legal_pod_run(
        self,
        *,
        legal: LegalReviewProjectionInput,
        permit: PermitReviewProjectionInput,
        actor_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Atomically append both projections from one Legal Pod run."""

        actor = self._actor(actor_id)
        created_at = self._now()
        with self._cursor() as cursor:
            legal_record = self._append(
                cursor,
                legal,
                actor_id=actor,
                created_at=created_at,
            )
            permit_record = self._append(
                cursor,
                permit,
                actor_id=actor,
                created_at=created_at,
            )
        return {
            "legalReview": self._public_item(legal_record),
            "permitReview": self._public_item(permit_record),
        }

    def append_legal_review(
        self,
        value: LegalReviewProjectionInput,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        actor = self._actor(actor_id)
        with self._cursor() as cursor:
            record = self._append(
                cursor,
                value,
                actor_id=actor,
                created_at=self._now(),
            )
        return self._public_item(record)

    def append_permit_review(
        self,
        value: PermitReviewProjectionInput,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        actor = self._actor(actor_id)
        with self._cursor() as cursor:
            record = self._append(
                cursor,
                value,
                actor_id=actor,
                created_at=self._now(),
            )
        return self._public_item(record)

    def list_legal_reviews(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._list("legal", limit)

    def list_permit_reviews(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._list("permit", limit)

    def _append(
        self,
        cursor: Any,
        value: ProjectionInput,
        *,
        actor_id: str,
        created_at: datetime,
    ) -> dict[str, Any]:
        kind = value.projection_kind
        table = _TABLES[kind]
        cursor.execute(
            f"""
            SELECT
                review_version_id, review_version, projection_sha256
            FROM {table}
            WHERE tenant_id = %s
              AND project_id = %s
              AND review_id = %s
            ORDER BY review_version DESC, created_at DESC
            LIMIT 1
            FOR SHARE
            """,
            (self.tenant_id, self.project_id, value.review_id),
        )
        previous = cursor.fetchone()
        review_version = int(previous[1]) + 1 if previous else 1
        supersedes_id = str(previous[0]) if previous else None
        record_id = f"project-{kind}-review-version:{uuid4()}"
        evidence = value.evidence.model_dump(mode="json", by_alias=True)
        parser = value.parser.model_dump(mode="json", by_alias=True)
        record_locator = value.record_locator.model_dump(
            mode="json",
            by_alias=True,
        )
        display = dict(value.display)
        canonical: dict[str, Any] = {
            "schemaVersion": "realytics-legal-review-projection-v1",
            "projectionKind": kind,
            "reviewVersionId": record_id,
            "tenantId": self.tenant_id,
            "projectId": self.project_id,
            "reviewId": value.review_id,
            "reviewVersion": review_version,
            "status": value.status,
            "reviewLevel": value.review_level,
            "evidence": evidence,
            "parser": parser,
            "analysisOnly": value.analysis_only,
            "reportEligible": value.report_eligible,
            "humanReviewStatus": value.human_review_status,
            "recordLocator": record_locator,
            "display": display,
            "supersedesReviewVersionId": supersedes_id,
            "createdBy": actor_id,
            "createdAt": created_at.isoformat(),
        }
        cursor.execute(
            f"""
            INSERT INTO {table} (
                review_version_id, tenant_id, project_id,
                review_id, review_version, status, review_level,
                evidence_status, evidence_locator_count,
                verified_evidence_count, verified_reference_count,
                verified_citation_count, required_claim_count,
                verified_claim_count, claim_coverage_complete,
                open_conflict_count, evidence_stale,
                citation_verification_status,
                legal_evidence_snapshot_id,
                snapshot_version, snapshot_sha256, snapshot_gate_eligible,
                required_claim_set_id, required_claim_set_sha256,
                parser_status, parser_module_key, parser_module_version,
                parser_input_sha256, parser_output_sha256,
                parser_failure_reason, analysis_only, report_eligible,
                human_review_status, record_locator_json, evidence_json,
                parser_json, display_json,
                supersedes_review_version_id, created_by, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                %s::jsonb, %s::jsonb, %s, %s, %s
            )
            RETURNING
                review_version_id,
                review_version,
                projection_hash_contract,
                projection_sha256,
                created_at
            """,
            (
                record_id,
                self.tenant_id,
                self.project_id,
                value.review_id,
                review_version,
                value.status,
                value.review_level,
                value.evidence.status,
                len(value.evidence.evidence_locators),
                value.evidence.verified_evidence_count,
                value.evidence.verified_reference_count,
                value.evidence.verified_citation_count,
                value.evidence.required_claim_count,
                value.evidence.verified_claim_count,
                value.evidence.claim_coverage_complete,
                value.evidence.open_conflict_count,
                value.evidence.stale,
                value.evidence.citation_verification_status,
                value.evidence.legal_evidence_snapshot_id,
                value.evidence.snapshot_version,
                _bare_hash(value.evidence.snapshot_sha256),
                value.evidence.snapshot_gate_eligible,
                value.evidence.required_claim_set_id,
                _bare_hash(value.evidence.required_claim_set_sha256),
                value.parser.status,
                value.parser.module_key,
                value.parser.module_version,
                _bare_hash(value.parser.input_sha256),
                _bare_hash(value.parser.output_sha256),
                value.parser.failure_reason,
                value.analysis_only,
                value.report_eligible,
                value.human_review_status,
                _json(record_locator),
                _json(evidence),
                _json(parser),
                _json(display),
                supersedes_id,
                actor_id,
                created_at,
            ),
        )
        inserted = cursor.fetchone()
        if (
            inserted is None
            or str(inserted[0]) != record_id
            or int(inserted[1] or 0) != review_version
            or str(inserted[2] or "") != "pg-jsonb-row-v1"
        ):
            raise ValueError(
                f"{kind} review projection was not inserted atomically"
            )
        database_created_at = inserted[4]
        canonical["reviewVersion"] = int(inserted[1])
        canonical["createdAt"] = (
            database_created_at.isoformat()
            if hasattr(database_created_at, "isoformat")
            else str(database_created_at)
        )
        canonical["projectionHashContract"] = str(inserted[2])
        canonical["projectionSha256"] = f"sha256:{inserted[3]}"
        return canonical

    def _list(self, kind: str, limit: int) -> list[dict[str, Any]]:
        table = _TABLES[kind]
        row_limit = max(1, min(int(limit), 500))
        with self._cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    review_version_id, review_id, review_version,
                    status, review_level, evidence_status,
                    evidence_locator_count, verified_evidence_count,
                    verified_reference_count, verified_citation_count,
                    required_claim_count, verified_claim_count,
                    claim_coverage_complete, open_conflict_count,
                    evidence_stale, citation_verification_status,
                    legal_evidence_snapshot_id, snapshot_version,
                    snapshot_sha256, snapshot_gate_eligible,
                    required_claim_set_id, required_claim_set_sha256,
                    parser_status, parser_module_key,
                    parser_module_version, parser_input_sha256,
                    parser_output_sha256, parser_failure_reason,
                    analysis_only, report_eligible, human_review_status,
                    record_locator_json, evidence_json, parser_json,
                    display_json, projection_hash_contract,
                    projection_sha256,
                    supersedes_review_version_id, created_by, created_at
                FROM {table}
                WHERE tenant_id = %s
                  AND project_id = %s
                ORDER BY created_at DESC, review_version DESC,
                         review_version_id DESC
                LIMIT %s
                """,
                (self.tenant_id, self.project_id, row_limit),
            )
            rows = cursor.fetchall()
        return [
            self._public_item(self._row(kind, row))
            for row in rows
        ]

    def _row(self, kind: str, row: Sequence[Any]) -> dict[str, Any]:
        evidence = self._mapping(row[32])
        parser = self._mapping(row[33])
        display = self._mapping(row[34])
        # Typed columns are authoritative. JSON copies are display/convenience
        # only and are overwritten here with the selected column values.
        evidence.update(
            {
                "status": str(row[5]),
                "verifiedEvidenceCount": int(row[7] or 0),
                "verifiedReferenceCount": int(row[8] or 0),
                "verifiedCitationCount": int(row[9] or 0),
                "requiredClaimCount": int(row[10] or 0),
                "verifiedClaimCount": int(row[11] or 0),
                "claimCoverageComplete": bool(row[12]),
                "openConflictCount": int(row[13] or 0),
                "stale": bool(row[14]),
                "citationVerificationStatus": str(row[15]),
                "legalEvidenceSnapshotId": row[16],
                "snapshotVersion": row[17],
                "snapshotSha256": (
                    f"sha256:{row[18]}" if row[18] else None
                ),
                "snapshotGateEligible": bool(row[19]),
                "requiredClaimSetId": row[20],
                "requiredClaimSetSha256": (
                    f"sha256:{row[21]}" if row[21] else None
                ),
            }
        )
        parser.update(
            {
                "status": str(row[22]),
                "moduleKey": row[23],
                "moduleVersion": row[24],
                "inputSha256": (
                    f"sha256:{row[25]}" if row[25] else None
                ),
                "outputSha256": (
                    f"sha256:{row[26]}" if row[26] else None
                ),
                "failureReason": row[27],
            }
        )
        created_at = (
            row[39].isoformat()
            if hasattr(row[39], "isoformat")
            else str(row[39])
        )
        return {
            "schemaVersion": "realytics-legal-review-projection-v1",
            "projectionKind": kind,
            "reviewVersionId": str(row[0]),
            "tenantId": self.tenant_id,
            "projectId": self.project_id,
            "reviewId": str(row[1]),
            "reviewVersion": int(row[2]),
            "status": str(row[3]),
            "reviewLevel": str(row[4]),
            "evidence": evidence,
            "parser": parser,
            "analysisOnly": bool(row[28]),
            "reportEligible": bool(row[29]),
            "humanReviewStatus": str(row[30]),
            "recordLocator": self._mapping(row[31]),
            "display": display,
            "projectionHashContract": str(row[35]),
            "projectionSha256": f"sha256:{row[36]}",
            "supersedesReviewVersionId": row[37],
            "createdBy": str(row[38]),
            "createdAt": created_at,
        }

    @staticmethod
    def _public_item(record: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(record)
        display = value.pop("display", {})
        if isinstance(display, Mapping):
            # Canonical typed fields win over display keys.
            value = {**dict(display), **value}
        evidence = value.get("evidence")
        if isinstance(evidence, Mapping):
            intelligence = value.get("legalIntelligence")
            intelligence = (
                dict(intelligence)
                if isinstance(intelligence, Mapping)
                else {}
            )
            intelligence.update(
                {
                    "evidenceStatus": evidence.get("status"),
                    "evidenceSnapshotId": evidence.get(
                        "legalEvidenceSnapshotId"
                    ),
                    "snapshotVersion": evidence.get("snapshotVersion"),
                    "snapshotSha256": evidence.get("snapshotSha256"),
                    "snapshotGateEligible": evidence.get(
                        "snapshotGateEligible"
                    )
                    is True,
                    "verifiedReferenceCount": evidence.get(
                        "verifiedReferenceCount"
                    ),
                    "verifiedCitationCount": evidence.get(
                        "verifiedCitationCount"
                    ),
                    "requiredClaimCount": evidence.get(
                        "requiredClaimCount"
                    ),
                    "verifiedClaimCount": evidence.get(
                        "verifiedClaimCount"
                    ),
                    "claimCoverageComplete": evidence.get(
                        "claimCoverageComplete"
                    )
                    is True,
                    "openConflictCount": evidence.get(
                        "openConflictCount"
                    ),
                    "stale": evidence.get("stale") is not False,
                    "citationVerification": {
                        "status": evidence.get(
                            "citationVerificationStatus"
                        )
                    },
                    "requiredClaimSetId": evidence.get(
                        "requiredClaimSetId"
                    ),
                    "requiredClaimSetSha256": evidence.get(
                        "requiredClaimSetSha256"
                    ),
                }
            )
            value["legalIntelligence"] = intelligence
            value["legalEvidenceSnapshotId"] = evidence.get(
                "legalEvidenceSnapshotId"
            )
            value["evidenceLocators"] = list(
                evidence.get("evidenceLocators") or []
            )
        parser = value.get("parser")
        if isinstance(parser, Mapping):
            value["parserStatus"] = parser.get("status")
        return value

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, (str, bytes, bytearray)):
            decoded = json.loads(value)
            return dict(decoded) if isinstance(decoded, Mapping) else {}
        return {}

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f'SET LOCAL search_path TO "{self._schema}", pg_catalog'
            )
            cursor.execute(
                "SELECT set_config('realytics.tenant_id', %s, true)",
                (self.tenant_id,),
            )
            yield cursor

    @staticmethod
    def _actor(actor_id: str) -> str:
        actor = str(actor_id or "").strip()
        if not actor or actor.casefold() in {
            "anonymous",
            "unknown",
            "system",
        }:
            raise ValueError(
                "actor_id must be an authenticated principal"
            )
        return actor


__all__ = ["PostgresLegalReviewProjectionRepository"]
