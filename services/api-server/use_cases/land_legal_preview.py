"""Compact, deterministic projection of an on-screen land/legal review."""

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any

from integrations.land_data_connectors import LandDataConnectorRouter
from integrations.vworld import build_pnu, fetch_parcel_by_pnu
from use_cases.parcel_pnu_resolution import canonical_project_parcel_rows, parcel_lookup_address


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


def manual_pnu_preview_project(project_id: str, pnus: Any) -> dict[str, Any]:
    """Create a disposable ledger from exact public cadastral matches."""
    if (
        not isinstance(pnus, list) or not 1 <= len(pnus) <= PARCEL_BATCH_SIZE
        or any(not isinstance(pnu, str) or not re.fullmatch(r"[0-9]{10}[12][0-9]{8}", pnu) for pnu in pnus)
        or len(set(pnus)) != len(pnus)
    ):
        raise ValueError("Invalid PNU list")
    rows = [_manual_parcel_row(pnu) for pnu in pnus]
    addresses = [row["addressFragment"] for row in rows if row.get("addressFragment")]
    jurisdictions = {row["pnu"][:5] for row in rows if row.get("pnu")}
    return {
        "project": {"id": project_id},
        "land": {"address": addresses[0]} if addresses and len(jurisdictions) == 1 else {},
        "landLedger": {"parcels": rows},
    }


def _manual_parcel_row(pnu: str) -> dict[str, Any]:
    parcel, diagnostic = fetch_parcel_by_pnu(pnu, timeout=2.0)
    parcel = parcel or {}
    address = str(parcel.get("addr") or parcel.get("cadastralAddress") or "").strip()
    lot = str(parcel.get("jibun") or "").strip()
    source = f"vworld:{pnu}:LP_PA_CBND_BUBUN"
    if not address or not lot:
        characteristics = LandDataConnectorRouter().lookup_parcel_address(pnu, timeout_seconds=2.0)
        if characteristics and characteristics.get("pnu") == pnu:
            dong = str(characteristics.get("legalDongName") or "").strip()
            lot = str(characteristics.get("lotNumber") or "").strip()
            address = f"{dong} {lot}" if dong and lot else ""
            parcel = characteristics
            source = characteristics.get("sourceUrlOrEvidenceId")
    address_lot = re.search(r"(?:^|\s)((?:산\s*)?[0-9]+(?:\s*-\s*[0-9]+)?)$", address)
    stated_lot = re.sub(r"\s+", "", address_lot[1]) if address_lot else ""
    if address and lot and not stated_lot:
        address = f"{address} {lot}"
    row: dict[str, Any] = {"requestedPnu": pnu}
    if str(parcel.get("pnu") or "").strip() != pnu:
        reason = "manual_pnu_mismatch" if parcel else {
            "pnu_mismatch": "manual_pnu_mismatch",
            "not_found": "manual_pnu_not_found",
            "skipped": "manual_pnu_source_unavailable",
        }.get(diagnostic.get("status"), "manual_pnu_lookup_failed")
    elif not address or not lot or not parcel_lookup_address({"addressFragment": address, "lotNumber": lot}, ""):
        reason = "manual_pnu_address_unavailable"
    elif build_pnu(pnu[:10], lot) != pnu or (stated_lot and build_pnu(pnu[:10], stated_lot) != pnu):
        reason = "manual_pnu_address_mismatch"
    else:
        return {
            **row,
            "pnu": pnu,
            "addressFragment": address,
            "lotNumber": lot,
            "pnuMatchMethod": "manual_pnu_exact",
            "pnuResolution": {"status": "resolved", "lotVerification": "verified", "resolvedAddress": address},
            "parcelSourceLocators": [{"sourceType": "api_row", "apiRowId": pnu, "sourceUrl": source}],
        }
    return {**row, "pnuResolution": {"status": "unresolved", "reason": reason}}


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
