"""Compact, deterministic projection of an on-screen land/legal review."""

from hashlib import sha256
import json
from typing import Any


def _fields(value: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value[key] for key in keys if key in value}


def compact_land_legal_review(review: dict[str, Any]) -> dict[str, Any]:
    analysis = review.get("developabilityAnalysis") or {}
    permit = analysis.get("permitRouteReview") or {}
    height = analysis.get("heightReview") or {}
    return {
        "permit": {
            **_fields(permit, "useClassification", "plannedDate", "approvalEvidenceStatus"),
            "routes": [_fields(route, "code", "label", "lawBasis") for route in permit.get("routes") or []],
            "approvalDocumentLocators": [
                _fields(source, "route", "sourceDocumentId", "pageNumber", "sheetName", "cell")
                for source in permit.get("approvalDocumentLocators") or []
            ],
        },
        "height": _fields(height, "jurisdictionCandidate", "plannedHeightM", "candidateLimitM", "confirmedLimitM"),
        "parcels": [
            {
                **_fields(parcel, "parcelIndex", "parcelLabel", "pnu", "zoning", "districtUnitPlan"),
                "sourceLocator": _fields(parcel.get("sourceLocator") or {}, "sourceType", "sourceDocumentId", "apiRowId", "evidenceId", "pageNumber", "sheetName", "cell"),
            }
            for parcel in height.get("parcelReviews") or []
        ],
    }


def compact_review_hash(value: dict[str, Any]) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
