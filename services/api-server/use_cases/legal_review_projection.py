"""Normalize Legal Pod output into tenant-native immutable projections."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from schemas.legal_review_projection import (
    LegalReviewProjectionInput,
    PermitReviewProjectionInput,
)


_EVIDENCE_IDENTITY_KEYS = (
    "sourceDocumentId",
    "evidenceId",
    "sourceUrl",
    "pageNumber",
    "sheetName",
    "cell",
    "apiRowId",
    "artifactKey",
    "resourceId",
    "resourceVersionId",
    "officialId",
    "contentHash",
)
_INTERNAL_RECORD_TYPES = {
    "legal_review",
    "legal_review_record",
    "permit_review",
    "permit_review_record",
}
_LEGAL_DISPLAY_KEYS = (
    "reviewType",
    "checks",
    "parcelLegalReviews",
    "riskIssues",
    "landEnrichment",
    "legalLifecycleAssessment",
    "lawChangeReport",
    "landPodHandoff",
    "buildableEnvelope",
    "nextActions",
    "pfHandoff",
    "mcp",
    "developabilityAnalysis",
    "requiredFollowUps",
    "disclaimer",
)
_PERMIT_DISPLAY_KEYS = (
    "permitCheck",
    "parserFailureReason",
)


def build_legal_pod_projection_inputs(
    result: Mapping[str, Any],
) -> tuple[LegalReviewProjectionInput, PermitReviewProjectionInput]:
    """Build strict normalized inputs without trusting display booleans."""

    legal_value = result.get("legalReview")
    permit_value = result.get("permitReview")
    if not isinstance(legal_value, Mapping):
        raise ValueError("Legal Pod result.legalReview is required")
    if not isinstance(permit_value, Mapping):
        raise ValueError("Legal Pod result.permitReview is required")
    legal = dict(legal_value)
    permit = dict(permit_value)
    legal_review_id = _required(legal.get("reviewId"), "legalReview.reviewId")
    permit_review_id = _required(
        permit.get("reviewId"),
        "permitReview.reviewId",
    )
    intelligence_value = legal.get("legalIntelligence")
    intelligence = (
        dict(intelligence_value)
        if isinstance(intelligence_value, Mapping)
        else {}
    )
    legal_locators = _external_evidence_locators(
        legal.get("evidenceLocators")
    )
    permit_locators = _external_evidence_locators(
        permit.get("sourceLocators")
    )
    citation_status = _citation_status(intelligence)
    legal_evidence_status = str(
        intelligence.get("evidenceStatus")
        or legal.get("sourceLocatorStatus")
        or "missing"
    ).strip().casefold()
    if legal_evidence_status not in {
        "missing",
        "weak",
        "partial",
        "verified",
        "complete",
        "unverified",
        "failed",
        "unavailable",
    }:
        legal_evidence_status = "unavailable"
    legal_input = LegalReviewProjectionInput(
        reviewId=legal_review_id,
        status=_required(legal.get("status"), "legalReview.status"),
        reviewLevel=_review_level(legal.get("reviewLevel")),
        evidence={
            "status": legal_evidence_status,
            "evidenceLocators": legal_locators,
            "verifiedEvidenceCount": _count(
                intelligence.get("verifiedReferenceCount"),
                intelligence.get("verifiedCitationCount"),
            ),
            "verifiedReferenceCount": _count(
                intelligence.get("verifiedReferenceCount")
            ),
            "verifiedCitationCount": _count(
                intelligence.get("verifiedCitationCount")
            ),
            "requiredClaimCount": _count(
                intelligence.get("requiredClaimCount")
            ),
            "verifiedClaimCount": _count(
                intelligence.get("verifiedClaimCount")
            ),
            "claimCoverageComplete": (
                intelligence.get("claimCoverageComplete") is True
            ),
            "openConflictCount": _open_conflict_count(intelligence),
            "stale": intelligence.get("stale") is not False,
            "citationVerificationStatus": citation_status,
            "legalEvidenceSnapshotId": (
                intelligence.get("evidenceSnapshotId")
                or legal.get("legalEvidenceSnapshotId")
            ),
            "snapshotVersion": intelligence.get("snapshotVersion"),
            "snapshotSha256": intelligence.get("snapshotSha256"),
            "snapshotGateEligible": (
                intelligence.get("snapshotGateEligible") is True
            ),
            "requiredClaimSetId": intelligence.get("requiredClaimSetId"),
            "requiredClaimSetSha256": intelligence.get(
                "requiredClaimSetSha256"
            ),
        },
        parser={"status": "not_applicable"},
        # A Legal Pod run is preliminary analysis. Evidence sealing alone never
        # promotes it to report-grade content.
        analysisOnly=True,
        reportEligible=False,
        humanReviewStatus=_human_review_status(
            legal.get("humanReviewStatus")
            or legal.get("reviewStatus")
        ),
        recordLocator={
            "sourceType": "legal_review_record",
            "reviewId": legal_review_id,
        },
        display=_display(legal, _LEGAL_DISPLAY_KEYS),
    )
    permit_parser_status = str(
        permit.get("parserStatus") or "not_available"
    ).strip().casefold()
    if permit_parser_status not in {
        "not_available",
        "unverified",
        "verified",
        "failed",
    }:
        permit_parser_status = "unverified"
    parser_projection: dict[str, Any] = {
        "status": permit_parser_status,
        "failureReason": permit.get("parserFailureReason"),
    }
    if permit_parser_status == "verified":
        # This names the deterministic in-process contract that selected only
        # approved, source-located permit/regulation documents. It is not
        # presented as a Parser V2 module.
        parser_projection.update(
            {
                "moduleKey": (
                    "legal_pod.verified_permit_document_evidence"
                ),
                "moduleVersion": "1",
                "outputSha256": "sha256:"
                + _sha256(
                    {
                        "status": permit_parser_status,
                        "evidenceStatus": permit.get("evidenceStatus"),
                        "evidenceLocators": permit_locators,
                    }
                ),
            }
        )
    permit_input = PermitReviewProjectionInput(
        reviewId=permit_review_id,
        status=_required(permit.get("status"), "permitReview.status"),
        reviewLevel=_review_level(permit.get("reviewLevel")),
        evidence={
            "status": _evidence_status(permit.get("evidenceStatus")),
            "evidenceLocators": permit_locators,
            "verifiedEvidenceCount": min(
                len(permit_locators),
                _count(permit.get("verifiedEvidenceCount")),
            ),
            "stale": True,
            "citationVerificationStatus": "not_verified",
        },
        parser=parser_projection,
        analysisOnly=True,
        reportEligible=False,
        humanReviewStatus=_human_review_status(
            permit.get("humanReviewStatus")
            or permit.get("reviewStatus")
        ),
        recordLocator={
            "sourceType": "permit_review_record",
            "reviewId": permit_review_id,
        },
        display=_display(permit, _PERMIT_DISPLAY_KEYS),
    )
    return legal_input, permit_input


def persist_legal_pod_projections(
    result: Mapping[str, Any],
    *,
    repository: Any,
    actor_id: str,
) -> dict[str, Any]:
    """Persist the Legal/Permit pair atomically and return safe identifiers."""

    legal, permit = build_legal_pod_projection_inputs(result)
    saved = repository.append_legal_pod_run(
        legal=legal,
        permit=permit,
        actor_id=actor_id,
    )
    saved_legal = saved["legalReview"]
    saved_permit = saved["permitReview"]
    return {
        "persisted": True,
        "reason": None,
        "legalReviewVersionId": saved_legal["reviewVersionId"],
        "legalReviewVersion": saved_legal["reviewVersion"],
        "legalReviewProjectionSha256": saved_legal["projectionSha256"],
        "permitReviewVersionId": saved_permit["reviewVersionId"],
        "permitReviewVersion": saved_permit["reviewVersion"],
        "permitReviewProjectionSha256": saved_permit["projectionSha256"],
    }


def mark_projection_persistence_failure(
    result: Mapping[str, Any],
    *,
    reason: str,
    error_type: str | None = None,
) -> dict[str, Any]:
    """Keep an unpersisted Legal Pod result explicitly analysis-only."""

    updated = deepcopy(dict(result))
    for key in ("legalReview", "permitReview"):
        review = updated.get(key)
        if not isinstance(review, dict):
            continue
        review["analysisOnly"] = True
        review["reportEligible"] = False
        review["normalizedProjectionEligible"] = False
    updated["normalizedProjectionPersistence"] = {
        "persisted": False,
        "reason": str(reason),
        "errorType": error_type,
    }
    return updated


def _external_evidence_locators(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        locator = {
            str(key): deepcopy(locator_value)
            for key, locator_value in item.items()
            if locator_value not in (None, "", [], {})
        }
        source_type = str(
            locator.get("sourceType")
            or locator.get("source_type")
            or ""
        ).strip().casefold()
        if source_type in _INTERNAL_RECORD_TYPES:
            continue
        if not any(
            locator.get(key) not in (None, "", [], {})
            for key in _EVIDENCE_IDENTITY_KEYS
        ):
            continue
        identity = _sha256(locator)
        if identity in seen:
            continue
        seen.add(identity)
        results.append(locator)
    return results


def _display(
    source: Mapping[str, Any],
    keys: Sequence[str],
) -> dict[str, Any]:
    return {
        key: deepcopy(source[key])
        for key in keys
        if source.get(key) not in (None, "", [], {})
    }


def _citation_status(intelligence: Mapping[str, Any]) -> str:
    citation = intelligence.get("citationVerification")
    if isinstance(citation, Mapping):
        value = str(
            citation.get("status") or "not_verified"
        ).strip().casefold()
        return (
            value
            if value
            in {
                "not_verified",
                "not_requested",
                "unverified",
                "conflicted",
                "partial",
                "verified",
                "failed",
                "stale",
            }
            else "not_verified"
        )
    return "not_verified"


def _evidence_status(value: Any) -> str:
    normalized = str(value or "missing").strip().casefold()
    return (
        normalized
        if normalized
        in {
            "missing",
            "weak",
            "partial",
            "verified",
            "complete",
            "unverified",
            "failed",
            "unavailable",
        }
        else "unavailable"
    )


def _review_level(value: Any) -> str:
    normalized = str(value or "preliminary").strip().casefold()
    return (
        normalized
        if normalized in {"preliminary", "full", "counsel"}
        else "preliminary"
    )


def _human_review_status(value: Any) -> str:
    normalized = str(value or "pending").strip().casefold()
    return (
        normalized
        if normalized
        in {
            "pending",
            "approved",
            "counsel_approved",
            "rejected",
            "needs_revision",
            "waived",
        }
        else "pending"
    )


def _open_conflict_count(intelligence: Mapping[str, Any]) -> int:
    explicit = intelligence.get("openConflictCount")
    if explicit is not None:
        return _count(explicit)
    conflicts = intelligence.get("openConflicts")
    if isinstance(conflicts, Mapping):
        return len(conflicts)
    if isinstance(conflicts, Sequence) and not isinstance(
        conflicts,
        (str, bytes, bytearray),
    ):
        return len(conflicts)
    return int(conflicts is True)


def _count(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "build_legal_pod_projection_inputs",
    "mark_projection_persistence_failure",
    "persist_legal_pod_projections",
]
