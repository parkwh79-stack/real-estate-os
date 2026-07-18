"""Parcel-weighted regulatory envelope and scenario eligibility contracts."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

from domains.feasibility.numeric import to_decimal


BUILDABLE_ENVELOPE_SCHEMA = "realytics-buildable-envelope-v1"
SITE_PARCEL_SNAPSHOT_SCHEMA = "realytics-site-parcel-snapshot-v1"
SCENARIO_ELIGIBILITY_SCHEMA = "realytics-feasibility-scenario-eligibility-v1"


def build_preliminary_buildable_envelope(
    project_data: Dict[str, Any],
    *,
    declared_land_area_sqm: Any = None,
    verified_approval_decision: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build a conservative parcel-capacity candidate from the project ledger.

    A weighted capacity is returned only when every included parcel has a valid
    unique PNU, area, and legal FAR and the declared site area reconciles. The
    arithmetic candidate remains report-ineligible until an approved regulatory
    snapshot explicitly approves the multi-parcel aggregation policy.
    """
    parcels = _parcels(project_data)
    if not parcels:
        return None

    included: List[Dict[str, Any]] = []
    parcel_membership = _parcel_membership_rows(parcels)
    excluded_count = 0
    zones: List[str] = []
    missing_pnu: List[int] = []
    invalid_pnu: List[str] = []
    missing_area: List[int] = []
    missing_far: List[int] = []
    missing_bcr: List[int] = []
    pnus: List[str] = []
    total_area = Decimal(0)
    capacity = Decimal(0)
    bcr_capacity = Decimal(0)

    for index, parcel in enumerate(parcels, start=1):
        if not isinstance(parcel, dict):
            continue
        if _is_excluded(parcel):
            excluded_count += 1
            continue
        lup = parcel.get("landUsePlanning") or parcel.get("land_use_planning") or {}
        if not isinstance(lup, dict):
            lup = {}
        pnu = str(parcel.get("pnu") or lup.get("pnu") or "").strip()
        area = _positive_decimal(
            parcel.get("includedAreaSqm")
            or parcel.get("included_area_sqm")
            or parcel.get("areaSqm")
            or parcel.get("area_sqm")
        )
        legal_far = _positive_decimal(
            lup.get("legalFar")
            or lup.get("legal_far")
            or parcel.get("legalFar")
            or parcel.get("legal_far")
        )
        legal_bcr = _positive_decimal(
            lup.get("legalBcr")
            or lup.get("legal_bcr")
            or parcel.get("legalBcr")
            or parcel.get("legal_bcr")
        )
        if not pnu:
            missing_pnu.append(index)
        elif not (len(pnu) == 19 and pnu.isdigit()):
            invalid_pnu.append(pnu)
        else:
            pnus.append(pnu)
        if area is None:
            missing_area.append(index)
        if legal_far is None:
            missing_far.append(index)
        if legal_bcr is None:
            missing_bcr.append(index)
        parcel_capacity = area * legal_far / Decimal(100) if area is not None and legal_far is not None else None
        parcel_bcr_capacity = area * legal_bcr / Decimal(100) if area is not None and legal_bcr is not None else None
        if area is not None:
            total_area += area
        if parcel_capacity is not None:
            capacity += parcel_capacity
        if parcel_bcr_capacity is not None:
            bcr_capacity += parcel_bcr_capacity
        for zone in lup.get("landUseZones") or lup.get("land_use_zones") or []:
            zone_text = str(zone).strip()
            if zone_text and zone_text not in zones:
                zones.append(zone_text)
        included.append({
            "parcelIndex": index,
            "pnu": pnu or None,
            "areaSqm": float(area) if area is not None else None,
            "legalFar": float(legal_far) if legal_far is not None else None,
            "legalBcr": float(legal_bcr) if legal_bcr is not None else None,
            "farCountedGfaCapacitySqm": float(parcel_capacity) if parcel_capacity is not None else None,
            "buildingFootprintCapacitySqm": float(parcel_bcr_capacity) if parcel_bcr_capacity is not None else None,
            "zones": [str(zone) for zone in (lup.get("landUseZones") or lup.get("land_use_zones") or []) if zone],
        })

    duplicate_pnus = sorted({pnu for pnu in pnus if pnus.count(pnu) > 1})
    blocking_codes: List[str] = []
    if not included:
        blocking_codes.append("NO_INCLUDED_PARCELS")
    if missing_pnu:
        blocking_codes.append("PNU_MISSING")
    if invalid_pnu:
        blocking_codes.append("PNU_INVALID")
    if duplicate_pnus:
        blocking_codes.append("PNU_DUPLICATE")
    if missing_area:
        blocking_codes.append("PARCEL_AREA_MISSING")
    if missing_far:
        blocking_codes.append("PARCEL_LEGAL_FAR_MISSING")

    declared = _positive_decimal(declared_land_area_sqm)
    area_difference = declared - total_area if declared is not None else None
    if declared is not None and abs(area_difference or Decimal(0)) > Decimal("0.1"):
        blocking_codes.append("SITE_AREA_MISMATCH")

    snapshot = project_data.get("regulatoryConstraintSnapshot") or project_data.get("regulatory_constraint_snapshot") or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    snapshot_id = snapshot.get("snapshotId") or snapshot.get("snapshot_id")
    snapshot_approved = str(snapshot.get("approvalStatus") or snapshot.get("approval_status") or "").lower() == "approved"
    policy = snapshot.get("aggregationPolicy") or snapshot.get("aggregation_policy") or {}
    policy_approved = bool(
        snapshot.get("aggregationApproved") is True
        or snapshot.get("aggregation_approved") is True
        or (isinstance(policy, dict) and policy.get("approved") is True)
    )
    complete = not blocking_codes
    effective_far = capacity / total_area * Decimal(100) if complete and total_area > 0 else None
    effective_bcr = (
        bcr_capacity / total_area * Decimal(100)
        if total_area > 0 and not missing_bcr
        else None
    )
    parcel_hash = _parcel_hash(parcel_membership)
    site_parcel_snapshot_id = (
        snapshot.get("siteParcelSnapshotId")
        or snapshot.get("site_parcel_snapshot_id")
        or project_data.get("siteParcelSnapshotId")
        or project_data.get("site_parcel_snapshot_id")
    )
    snapshot_parcel_hash = str(snapshot.get("parcelSetHash") or snapshot.get("parcel_set_hash") or "").strip()
    snapshot_source_hash = str(
        snapshot.get("sourceHash")
        or snapshot.get("source_hash")
        or snapshot.get("sourceDocumentHash")
        or snapshot.get("source_document_hash")
        or ""
    ).strip()
    snapshot_parcel_count = _integer(snapshot.get("parcelCount") or snapshot.get("parcel_count"))
    snapshot_included_count = _integer(
        snapshot.get("includedParcelCount") or snapshot.get("included_parcel_count")
    )
    effective_date = snapshot.get("effectiveDate") or snapshot.get("effective_date") or snapshot.get("asOfDate") or snapshot.get("as_of_date")
    approved_by = snapshot.get("approvedBy") or snapshot.get("approved_by")
    approval_decision_id = snapshot.get("approvalDecisionId") or snapshot.get("approval_decision_id")
    policy_id = policy.get("policyId") or policy.get("policy_id") or policy.get("name") if isinstance(policy, dict) else None
    policy_version = policy.get("version") or policy.get("policyVersion") or policy.get("policy_version") if isinstance(policy, dict) else None
    binding_checks = [
        _binding_check("snapshot_id", True, bool(snapshot_id)),
        _binding_check(
            "approval_status",
            "approved",
            str(snapshot.get("approvalStatus") or snapshot.get("approval_status") or "").lower(),
        ),
        _binding_check("approved_by", True, bool(approved_by)),
        _binding_check("approval_decision_id", True, bool(approval_decision_id)),
        _binding_check("effective_date", True, bool(effective_date)),
        _binding_check("source_sha256", True, _is_sha256(snapshot_source_hash)),
        _binding_check("site_parcel_snapshot_id", True, bool(site_parcel_snapshot_id)),
        _binding_check("parcel_set_hash", parcel_hash, snapshot_parcel_hash),
        _binding_check("parcel_count", len(parcels), snapshot_parcel_count),
        _binding_check("included_parcel_count", len(included), snapshot_included_count),
        _binding_check("aggregation_policy_approved", True, policy_approved),
        _binding_check("aggregation_policy_id", True, bool(policy_id)),
        _binding_check("aggregation_policy_version", True, bool(policy_version)),
        _binding_check(
            "approval_repository_verification",
            True,
            _verified_approval_matches(
                verified_approval_decision,
                approval_decision_id=approval_decision_id,
                snapshot_id=snapshot_id,
                parcel_set_hash=parcel_hash,
                source_hash=snapshot_source_hash,
            ),
        ),
    ]
    binding_complete = all(check["passed"] for check in binding_checks)
    aggregation_approved = complete and snapshot_approved and binding_complete
    warnings: List[str] = []
    if complete and not aggregation_approved:
        warnings.append("AGGREGATION_POLICY_APPROVAL_REQUIRED")
        warnings.extend(
            f"REGULATORY_BINDING_{check['name'].upper()}"
            for check in binding_checks
            if not check["passed"]
        )

    if not complete:
        eligibility = "not_assessable"
        approval_status = "not_assessable"
    elif aggregation_approved:
        eligibility = "eligible"
        approval_status = "approved"
    else:
        eligibility = "conditional"
        approval_status = "candidate"

    return {
        "schemaVersion": BUILDABLE_ENVELOPE_SCHEMA,
        "siteParcelSnapshot": {
            "schemaVersion": SITE_PARCEL_SNAPSHOT_SCHEMA,
            "siteParcelSnapshotId": site_parcel_snapshot_id,
            "parcelSetHash": parcel_hash,
            "parcelCount": len(parcels),
            "includedParcelCount": len(included),
            "excludedParcelCount": excluded_count,
            "uniquePnuCount": len(set(pnus)),
            "pnuCoverage": _coverage(len(included) - len(missing_pnu) - len(invalid_pnu), len(included)),
            "areaCoverage": _coverage(len(included) - len(missing_area), len(included)),
            "legalFarCoverage": _coverage(len(included) - len(missing_far), len(included)),
            "legalBcrCoverage": _coverage(len(included) - len(missing_bcr), len(included)),
            "duplicatePnus": duplicate_pnus,
            "totalIncludedAreaSqm": float(total_area) if total_area else None,
            "declaredLandAreaSqm": float(declared) if declared is not None else None,
            "areaDifferenceSqm": float(area_difference) if area_difference is not None else None,
            "parcels": included,
            "parcelMembership": parcel_membership,
        },
        "regulatoryConstraintSnapshotId": snapshot_id,
        "aggregationPolicy": "sum_parcel_area_times_legal_far",
        "aggregationApprovalStatus": approval_status,
        "farCountedGfaCapacitySqm": float(capacity) if complete else None,
        "effectiveLegalFar": float(effective_far) if effective_far is not None else None,
        "buildingFootprintCapacitySqm": float(bcr_capacity) if effective_bcr is not None else None,
        "effectiveLegalBcr": float(effective_bcr) if effective_bcr is not None else None,
        "zones": zones,
        "eligibility": eligibility,
        "reportGrade": bool(aggregation_approved),
        "blockingCodes": blocking_codes,
        "warningCodes": warnings,
        "snapshotBindingChecks": binding_checks,
        "regulatoryApproval": {
            "approvalStatus": snapshot.get("approvalStatus") or snapshot.get("approval_status"),
            "approvedBy": approved_by,
            "approvalDecisionId": approval_decision_id,
            "effectiveDate": effective_date,
            "sourceHash": snapshot_source_hash or None,
            "policyId": policy_id,
            "policyVersion": policy_version,
        },
        "formulaTrace": {
            "formulaId": "regulation.parcel_weighted_far.v1",
            "formulaVersion": "1.0.0",
            "formula": "sum(parcel_area_sqm * parcel_legal_far / 100)",
            "inputSnapshotHash": parcel_hash,
            "roundingPolicy": "none_decimal",
        },
    }


def evaluate_program_eligibility(
    *,
    applied_far: Any,
    legal_far: Any,
    constraint_snapshot_id: Optional[str] = None,
    constraint_approved: bool = False,
    scenario_class: str = "current_rights",
) -> Dict[str, Any]:
    applied = _positive_decimal(applied_far)
    legal = _positive_decimal(legal_far)
    blocking: List[str] = []
    conditions: List[str] = []
    if applied is None:
        blocking.append("APPLIED_FAR_MISSING")
    if legal is None:
        blocking.append("LEGAL_FAR_MISSING")
    if blocking:
        eligibility = "not_assessable"
    elif applied > legal:
        eligibility = "ineligible"
        blocking.append("LEGAL_FAR_EXCEEDED")
    elif constraint_approved:
        eligibility = "eligible"
    else:
        eligibility = "conditional"
        conditions.append("CONSTRAINT_SNAPSHOT_APPROVAL_REQUIRED")
    return {
        "schemaVersion": SCENARIO_ELIGIBILITY_SCHEMA,
        "eligibility": eligibility,
        "scenarioClass": scenario_class,
        "constraintSnapshotId": constraint_snapshot_id,
        "appliedFar": float(applied) if applied is not None else None,
        "legalFar": float(legal) if legal is not None else None,
        "blockingConstraintIds": blocking,
        "conditionIds": conditions,
        "reportGrade": eligibility == "eligible" and bool(constraint_snapshot_id),
    }


def _parcels(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    ledger = project_data.get("landLedger") or project_data.get("land_ledger") or {}
    parcels = ledger.get("parcels") if isinstance(ledger, dict) else None
    if not isinstance(parcels, list):
        parcels = project_data.get("land_acquisition") or []
    return [parcel for parcel in parcels if isinstance(parcel, dict)]


def _is_excluded(parcel: Dict[str, Any]) -> bool:
    return bool(
        parcel.get("excludedFromProject") is True
        or parcel.get("excluded_from_project") is True
        or parcel.get("includedInProject") is False
        or parcel.get("included_in_project") is False
    )


def _positive_decimal(value: Any) -> Optional[Decimal]:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = to_decimal(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _coverage(covered: int, total: int) -> float:
    return round(covered / total, 6) if total else 0.0


def _parcel_hash(parcels: List[Dict[str, Any]]) -> str:
    canonical = json.dumps(parcels, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parcel_membership_rows(parcels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for parcel in parcels:
        lup = parcel.get("landUsePlanning") or parcel.get("land_use_planning") or {}
        if not isinstance(lup, dict):
            lup = {}
        area = _positive_decimal(
            parcel.get("includedAreaSqm")
            or parcel.get("included_area_sqm")
            or parcel.get("areaSqm")
            or parcel.get("area_sqm")
        )
        legal_far = _positive_decimal(
            lup.get("legalFar")
            or lup.get("legal_far")
            or parcel.get("legalFar")
            or parcel.get("legal_far")
        )
        legal_bcr = _positive_decimal(
            lup.get("legalBcr")
            or lup.get("legal_bcr")
            or parcel.get("legalBcr")
            or parcel.get("legal_bcr")
        )
        rows.append({
            "pnu": str(parcel.get("pnu") or lup.get("pnu") or "").strip() or None,
            "included": not _is_excluded(parcel),
            "areaSqm": str(area.normalize()) if area is not None else None,
            "legalFar": str(legal_far.normalize()) if legal_far is not None else None,
            "legalBcr": str(legal_bcr.normalize()) if legal_bcr is not None else None,
        })
    return sorted(rows, key=lambda row: (str(row.get("pnu") or ""), json.dumps(row, sort_keys=True)))


def _binding_check(name: str, expected: Any, actual: Any) -> Dict[str, Any]:
    return {
        "name": name,
        "expected": expected,
        "actual": actual,
        "passed": actual == expected,
    }


def _integer(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)


def _verified_approval_matches(
    decision: Optional[Dict[str, Any]],
    *,
    approval_decision_id: Any,
    snapshot_id: Any,
    parcel_set_hash: str,
    source_hash: str,
) -> bool:
    if not isinstance(decision, dict):
        return False
    decision_status = str(decision.get("approvalStatus") or decision.get("approval_status") or "").lower()
    return bool(
        decision_status == "approved"
        and str(decision.get("approvalDecisionId") or decision.get("approval_decision_id") or "")
        == str(approval_decision_id or "")
        and str(decision.get("snapshotId") or decision.get("snapshot_id") or "") == str(snapshot_id or "")
        and str(decision.get("parcelSetHash") or decision.get("parcel_set_hash") or "") == parcel_set_hash
        and str(decision.get("sourceHash") or decision.get("source_hash") or "") == source_hash
    )
