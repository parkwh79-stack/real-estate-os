"""Compact, deterministic projection of an on-screen land/legal review."""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

from use_cases.parcel_pnu_resolution import canonical_project_parcel_rows


PARCEL_BATCH_SIZE = 5
PREVIEW_LOCATOR_KEYS = (
    "sourceType", "sourceDocumentId", "sourceUrl", "apiRowId", "evidenceId",
    "pageNumber", "sheetName", "cell", "rowNumber",
)


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


@dataclass(frozen=True)
class ParcelPreviewBatch:
    revision: str
    indexes: tuple[int, ...]
    total: int
    next_offset: int | None


class StaleParcelPreview(ValueError):
    """The project parcels changed since the caller began previewing."""


def parcel_preview_batch(project_id: str, project: dict[str, Any], inputs: dict[str, Any]) -> ParcelPreviewBatch:
    rows = canonical_project_parcel_rows(project)
    land = project.get("land") or {}
    revision = compact_review_hash({
        "projectId": project_id,
        "address": land.get("address") if isinstance(land, dict) else None,
        "parcels": rows,
    })
    expected = inputs.get("parcelRevision")
    if expected is not None and (
        not isinstance(expected, str) or len(expected) != 64
        or any(char not in "0123456789abcdef" for char in expected)
    ):
        raise ValueError("Invalid parcel revision")
    if expected is not None and expected != revision:
        raise StaleParcelPreview("Project parcels changed")

    total = len(rows)
    requested_indexes = inputs.get("parcelIndexes")
    offset = inputs.get("parcelOffset", 0)
    if requested_indexes is not None:
        if (
            "parcelOffset" in inputs or expected is None
            or not isinstance(requested_indexes, list)
            or not 1 <= len(requested_indexes) <= PARCEL_BATCH_SIZE
            or any(type(index) is not int or not 1 <= index <= total for index in requested_indexes)
            or len(set(requested_indexes)) != len(requested_indexes)
        ):
            raise ValueError("Invalid parcel indexes")
        return ParcelPreviewBatch(revision, tuple(sorted(requested_indexes)), total, None)

    if (
        type(offset) is not int or offset < 0
        or (offset != 0 and (expected is None or offset >= total))
    ):
        raise ValueError("Invalid parcel offset")
    end = min(total, offset + PARCEL_BATCH_SIZE)
    return ParcelPreviewBatch(revision, tuple(range(offset + 1, end + 1)), total, end if end < total else None)


def compact_land_legal_preview(result: dict[str, Any]) -> dict[str, Any]:
    """Bind saved selections to the exact parcel page that was reviewed."""
    compact = compact_land_legal_review(result["legalReview"])
    batch = result["parcelBatch"]
    rows = {row["parcelIndex"]: row for row in batch["rows"]}
    # Bind public rows and statuses without including ephemeral lookup timings.
    compact["parcelBatch"] = {
        "parcelRevision": batch["parcelRevision"],
        "rows": [
            _fields(row, "parcelIndex", "status", "landUseStatus", "pnu", "zoning", "districtUnitPlan")
            for row in rows.values()
        ],
    }
    compact["parcels"] = [
        {
            **_fields(row, "parcelIndex", "parcelLabel", "pnu", "zoning", "districtUnitPlan"),
            "sourceLocator": _fields(row.get("landUseSourceLocator") or {}, *PREVIEW_LOCATOR_KEYS),
            "sourceLocators": [
                _fields(locator, *PREVIEW_LOCATOR_KEYS)
                for locator in row["sourceLocators"]
            ],
        }
        for row in rows.values() if row["status"] == "confirmed"
    ]
    return compact
