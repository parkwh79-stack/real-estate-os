from __future__ import annotations

import hashlib
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from integrations.market_data import MolitRtmsConnector, lawd_cd_from_pnu
from storage import list_deal_state_records, load_state, storage_backend, upsert_deal_state_record
from use_cases.intake_finalize import resolve_address_to_parcel
from use_cases.market_comparables import (
    fetch_project_market_comparables,
    inject_market_comparables,
    recent_deal_yms,
)


AUTO_SUPPLEMENT_SCHEMA_VERSION = "realytics-auto-evidence-supplement-v1"


def auto_supplement_validation_gaps(
    project_id: str,
    pod_key: str,
    validation_result: Dict[str, Any],
    project_data: Dict[str, Any],
    *,
    actor: str = "chief_deal_orchestrator",
) -> Dict[str, Any]:
    """Try to fill parser-validation gaps before asking users for more files.

    This layer never fabricates final facts. It creates one of three auditable
    records:
    - evidence: public/internal source-backed value
    - assumption: benchmark/proxy candidate that requires review
    - auto_supplement_attempt: honest failed or skipped attempt
    """
    now = _now()
    findings = validation_result.get("findings") or []
    attempts: List[Dict[str, Any]] = []
    created_records: List[Dict[str, Any]] = []

    for finding in findings:
        risk_code = str(finding.get("riskCode") or "")
        canonical = str((finding.get("details") or {}).get("canonicalField") or "")
        if risk_code not in {
            "PARSED_FACT_REQUIRED_MISSING",
            "PROPOSED_PRICE_BASIS_MISSING",
            "PARSED_FACT_SOURCE_LOCATOR_WEAK",
            "PARSED_FACT_VALUE_CONFLICT",
        }:
            continue

        supplement = _supplement_for_canonical(
            project_id=project_id,
            pod_key=pod_key,
            canonical=canonical,
            risk_code=risk_code,
            finding=finding,
            project_data=project_data,
            actor=actor,
            now=now,
        )
        if not supplement:
            continue
        attempts.append(supplement)
        for record in supplement.get("records") or []:
            created_records.append(upsert_deal_state_record(project_id, record))
        _update_related_risks(
            project_id,
            pod_key=pod_key,
            risk_code=risk_code,
            canonical=canonical,
            supplement=supplement,
            now=now,
        )

    attempt_record = upsert_deal_state_record(
        project_id,
        {
            "schemaVersion": AUTO_SUPPLEMENT_SCHEMA_VERSION,
            "recordType": "auto_supplement_attempt",
            "recordId": f"auto_supplement_attempt:{pod_key}:{_safe_key(now)}",
            "key": f"auto_supplement.{pod_key}",
            "podKey": pod_key,
            "sourceType": "auto_evidence_supplement",
            "sourceLocator": {
                "type": "parsed_fact_validation",
                "validationRecordId": (validation_result.get("validationRecord") or {}).get("recordId"),
                "podKey": pod_key,
            },
            "attemptedBy": actor,
            "attemptedAt": now,
            "attempts": [_attempt_public(a) for a in attempts],
            "summary": {
                "attemptCount": len(attempts),
                "createdRecordCount": len(created_records),
                "supplementedCount": sum(1 for item in attempts if item.get("status") == "supplemented"),
                "needsUserMaterialCount": sum(1 for item in attempts if item.get("status") == "needs_user_material"),
                "failedCount": sum(1 for item in attempts if item.get("status") in {"failed", "not_available"}),
            },
        },
    )
    return {
        "schemaVersion": AUTO_SUPPLEMENT_SCHEMA_VERSION,
        "projectId": project_id,
        "podKey": pod_key,
        "attemptedAt": now,
        "summary": attempt_record.get("summary") or {},
        "attemptRecord": attempt_record,
        "attempts": [_attempt_public(a) for a in attempts],
        "createdRecords": created_records,
    }


def _supplement_for_canonical(
    *,
    project_id: str,
    pod_key: str,
    canonical: str,
    risk_code: str,
    finding: Dict[str, Any],
    project_data: Dict[str, Any],
    actor: str,
    now: str,
) -> Dict[str, Any] | None:
    if canonical in {"land.pnu", "landEvidence.officialLandPrice.pricePerSqm"}:
        return _supplement_land_public_data(project_id, pod_key, canonical, project_data, actor=actor, now=now)
    if canonical == "registry":
        return _request_registry_evidence(project_id, pod_key, finding, actor=actor, now=now)
    if canonical in {"marketComparable.pricePerPy", "market.proposedPricePerPy"}:
        return _supplement_market_benchmark(project_id, pod_key, canonical, project_data, actor=actor, now=now)
    if canonical in {
        "feasibility_input.total_revenue",
        "feasibility_input.total_cost",
        "feasibility_input.construction_cost",
        "feasibility_input.land_cost",
        "pf_structure.amount.sum",
        "pf.ltv",
        "pf.exitSaleRate",
        "waterfall",
    }:
        return _supplement_from_project_or_history(project_id, pod_key, canonical, project_data, actor=actor, now=now)
    if risk_code == "PARSED_FACT_VALUE_CONFLICT":
        return _create_conflict_resolution_candidate(project_id, pod_key, finding, actor=actor, now=now)
    return None


def _supplement_land_public_data(
    project_id: str,
    pod_key: str,
    canonical: str,
    project_data: Dict[str, Any],
    *,
    actor: str,
    now: str,
) -> Dict[str, Any]:
    land = project_data.setdefault("land", {})
    if not land.get("pnu"):
        address = str(land.get("address") or _first_parcel_address(project_data) or "").strip()
        if address:
            try:
                resolved = resolve_address_to_parcel(address)
            except Exception as exc:
                return _failed_attempt(pod_key, canonical, "VWorld", f"VWorld lookup failed: {type(exc).__name__}")
            if resolved.get("status") == "resolved" and resolved.get("pnu"):
                land["pnu"] = resolved.get("pnu")
                land["address"] = land.get("address") or address
                if resolved.get("officialLandPricePerSqm"):
                    land["officialLandPricePerSqm"] = resolved.get("officialLandPricePerSqm")
                    land["officialLandPriceBaseYear"] = resolved.get("officialLandPriceBaseYear")
        if not land.get("pnu"):
            return _failed_attempt(pod_key, canonical, "VWorld", "No project address or VWorld PNU result was available.")

    records: List[Dict[str, Any]] = []
    pnu = str(land.get("pnu"))
    records.append(
        _supplemented_fact_record(
            project_id,
            pod_key,
            "land.pnu",
            pnu,
            source_type="public_api",
            source_authority="official_public_api",
            source_locator={"sourceName": "VWorld cadastral data", "pnu": pnu, "fetchedAt": now},
            confidence=0.92,
            now=now,
            actor=actor,
        )
    )
    records.append(
        _evidence_record(
            project_id,
            "land.pnu",
            pnu,
            evidence_type="pnu_resolution",
            source_locator={"sourceName": "VWorld cadastral data", "pnu": pnu, "fetchedAt": now},
            confidence=0.92,
            now=now,
        )
    )
    if land.get("officialLandPricePerSqm"):
        value = land.get("officialLandPricePerSqm")
        locator = {
            "sourceName": land.get("officialLandPriceSource") or "VWorld cadastral jiga",
            "pnu": pnu,
            "baseYear": land.get("officialLandPriceBaseYear"),
            "fetchedAt": now,
        }
        records.append(
            _supplemented_fact_record(
                project_id,
                pod_key,
                "landEvidence.officialLandPrice.pricePerSqm",
                value,
                source_type="public_api",
                source_authority="official_public_api",
                source_locator=locator,
                confidence=0.9,
                now=now,
                actor=actor,
                unit="KRW/sqm",
            )
        )
        records.append(
            _evidence_record(
                project_id,
                "landEvidence.officialLandPrice.pricePerSqm",
                value,
                evidence_type="official_land_price",
                source_locator=locator,
                confidence=0.9,
                now=now,
                unit="KRW/sqm",
            )
        )
    return _supplemented_attempt(pod_key, canonical, "VWorld cadastral data", records)


def _request_registry_evidence(
    project_id: str,
    pod_key: str,
    finding: Dict[str, Any],
    *,
    actor: str,
    now: str,
) -> Dict[str, Any]:
    canonical = str((finding.get("details") or {}).get("canonicalField") or "registry")
    record = {
        "schemaVersion": "realytics-evidence-request-v1",
        "recordType": "evidence",
        "recordId": f"evidence_request:registry:{_safe_key(pod_key)}",
        "key": "registry.evidence_request",
        "evidenceType": "registry_record_request",
        "evidenceStatus": "needs_user_material",
        "value": "registry_or_encumbrance_evidence_required",
        "sourceType": "auto_evidence_supplement",
        "sourceLocator": {
            "type": "missing_evidence_request",
            "requiredEvidence": "registry_record",
            "reason": "Registry data cannot be invented or scraped without a licensed/manual evidence source.",
        },
        "requestedBy": actor,
        "requestedAt": now,
        "confidence": 1.0,
    }
    return {
        "podKey": pod_key,
        "canonicalField": canonical,
        "status": "needs_user_material",
        "source": "manual_registry_evidence",
        "reason": "Registry/encumbrance evidence requires uploaded or licensed evidence.",
        "records": [record],
    }


def _supplement_market_benchmark(
    project_id: str,
    pod_key: str,
    canonical: str,
    project_data: Dict[str, Any],
    *,
    actor: str,
    now: str,
) -> Dict[str, Any]:
    records = _market_comparables(project_data)
    if not records:
        records = _fetch_and_inject_market_comparables(project_data)
    if not records:
        return _failed_attempt(
            pod_key,
            canonical,
            "MOLIT RTMS / project market evidence",
            "No market comparables were available from project memory or configured public APIs.",
        )

    prices = [_to_float(record.get("pricePerPy")) for record in records]
    prices = [price for price in prices if price and price > 0]
    if not prices:
        return _failed_attempt(pod_key, canonical, "market comparables", "Comparable records had no usable pricePerPy.")

    median_price = round(statistics.median(prices))
    locator = {
        "sourceName": "Market comparable benchmark",
        "sourceTypes": sorted({str(record.get("sourceType") or record.get("source") or "unknown") for record in records}),
        "comparableCount": len(records),
        "sampleEvidenceIds": [
            record.get("sourceUrlOrEvidenceId") or record.get("evidenceId") or record.get("name")
            for record in records[:8]
        ],
        "fetchedAt": now,
    }
    fact_key = canonical
    value_role = "project_proposed_price" if _project_has_proposed_price(project_data) else "benchmark_proxy_not_project_proposed_price"
    if canonical == "market.proposedPricePerPy" and value_role != "project_proposed_price":
        risk_code = "PROPOSED_PRICE_BASIS_SUPPLEMENTED_NEEDS_REVIEW"
    else:
        risk_code = "MARKET_COMPARABLES_SUPPLEMENTED_NEEDS_REVIEW"

    created = [
        _supplemented_fact_record(
            project_id,
            pod_key,
            fact_key,
            median_price,
            source_type="public_or_internal_comparable",
            source_authority="benchmark_proxy" if value_role != "project_proposed_price" else "project_memory",
            source_locator=locator,
            confidence=0.72 if value_role != "project_proposed_price" else 0.84,
            now=now,
            actor=actor,
            unit="KRW/py",
            extra={"valueRole": value_role, "reviewRequired": value_role != "project_proposed_price"},
        ),
        _assumption_candidate_record(
            project_id,
            fact_key,
            median_price,
            assumption_type=value_role,
            source_locator=locator,
            confidence=0.72,
            now=now,
            unit="KRW/py",
        ),
        _supplement_risk_record(
            project_id,
            pod_key,
            risk_code,
            fact_key,
            f"{fact_key} was supplemented from {len(records)} comparable record(s); reviewer approval is required before report-grade use.",
            locator,
            now=now,
        ),
    ]
    return _supplemented_attempt(pod_key, canonical, "market comparables", created)


def _supplement_from_project_or_history(
    project_id: str,
    pod_key: str,
    canonical: str,
    project_data: Dict[str, Any],
    *,
    actor: str,
    now: str,
) -> Dict[str, Any]:
    current = _value_from_project(project_data, canonical)
    if current is not None:
        locator = {"sourceName": "project_memory", "canonicalField": canonical}
        return _supplemented_attempt(
            pod_key,
            canonical,
            "project_memory",
            [
                _supplemented_fact_record(
                    project_id,
                    pod_key,
                    canonical,
                    current,
                    source_type="project_memory",
                    source_authority="uploaded_or_project_state",
                    source_locator=locator,
                    confidence=0.78,
                    now=now,
                    actor=actor,
                    unit=_unit_for(canonical),
                ),
                _evidence_record(
                    project_id,
                    canonical,
                    current,
                    evidence_type="project_memory_value",
                    source_locator=locator,
                    confidence=0.78,
                    now=now,
                    unit=_unit_for(canonical),
                ),
            ],
        )

    historical = _historical_candidate(project_id, canonical)
    if historical is None:
        return _failed_attempt(
            pod_key,
            canonical,
            "historical_project_snapshots",
            "No current project value or historical comparable candidate was available.",
        )
    locator = {
        "sourceName": "historical_project_snapshots",
        "canonicalField": canonical,
        "sampleSize": historical["sampleSize"],
        "snapshotIds": historical["snapshotIds"],
    }
    value = historical["value"]
    return _supplemented_attempt(
        pod_key,
        canonical,
        "historical_project_snapshots",
        [
            _supplemented_fact_record(
                project_id,
                pod_key,
                canonical,
                value,
                source_type="historical_comparable",
                source_authority="benchmark_proxy",
                source_locator=locator,
                confidence=historical["confidence"],
                now=now,
                actor=actor,
                unit=_unit_for(canonical),
                extra={"valueRole": "historical_benchmark_proxy", "reviewRequired": True},
            ),
            _assumption_candidate_record(
                project_id,
                canonical,
                value,
                assumption_type="historical_benchmark_proxy",
                source_locator=locator,
                confidence=historical["confidence"],
                now=now,
                unit=_unit_for(canonical),
            ),
            _supplement_risk_record(
                project_id,
                pod_key,
                "HISTORICAL_BENCHMARK_SUPPLEMENTED_NEEDS_REVIEW",
                canonical,
                f"{canonical} was supplemented from historical comparable snapshots; reviewer approval is required.",
                locator,
                now=now,
            ),
        ],
    )


def _create_conflict_resolution_candidate(
    project_id: str,
    pod_key: str,
    finding: Dict[str, Any],
    *,
    actor: str,
    now: str,
) -> Dict[str, Any]:
    details = finding.get("details") or {}
    canonical = str(details.get("canonicalField") or "unknown")
    values = [v for v in [details.get("minValue"), details.get("maxValue")] if isinstance(v, (int, float))]
    if not values:
        return _failed_attempt(pod_key, canonical, "parsed_fact_conflict", "Conflict had no numeric values to propose a conservative candidate.")
    conservative = min(values)
    locator = finding.get("sourceLocator") or {"type": "parsed_fact_conflict", "canonicalField": canonical}
    return _supplemented_attempt(
        pod_key,
        canonical,
        "parsed_fact_conflict_conservative_candidate",
        [
            _assumption_candidate_record(
                project_id,
                canonical,
                conservative,
                assumption_type="conservative_conflict_resolution_candidate",
                source_locator=locator,
                confidence=0.6,
                now=now,
                unit=_unit_for(canonical),
            ),
            _supplement_risk_record(
                project_id,
                pod_key,
                "PARSED_FACT_CONFLICT_CANDIDATE_NEEDS_APPROVAL",
                canonical,
                f"{canonical} has conflicting values. A conservative candidate was proposed for reviewer approval.",
                locator,
                now=now,
            ),
        ],
    )


def _supplemented_fact_record(
    project_id: str,
    pod_key: str,
    canonical: str,
    value: Any,
    *,
    source_type: str,
    source_authority: str,
    source_locator: Dict[str, Any],
    confidence: float,
    now: str,
    actor: str,
    unit: str | None = None,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "schemaVersion": "realytics-parsed-fact-v1",
        "projectId": project_id,
        "recordType": "parsed_fact",
        "recordId": f"parsed_fact:auto_supplement:{_safe_key(pod_key)}:{_safe_key(canonical)}:{_hash(source_locator)}",
        "key": canonical,
        "factKey": canonical,
        "canonicalField": canonical,
        "parserPath": f"auto_supplement.{canonical}",
        "domain": canonical.split(".", 1)[0],
        "value": value,
        "normalizedValue": value,
        "unit": unit,
        "rawText": str(value),
        "normalizedSurface": str(value),
        "textQualityFlags": {},
        "termId": _term_for(canonical),
        "sourceType": source_type,
        "sourceAuthority": source_authority,
        "factOrigin": "auto_supplement",
        "sourceLocator": source_locator,
        "locatorStatus": "located",
        "locatorPrecision": _locator_precision(source_locator),
        "targetPods": [pod_key],
        "critical": canonical in _critical_fields(),
        "validationStatus": "needs_review" if source_authority != "official_public_api" else "parsed",
        "confidence": confidence,
        "createdBy": actor,
        "createdAt": now,
        **(extra or {}),
    }


def _evidence_record(
    project_id: str,
    canonical: str,
    value: Any,
    *,
    evidence_type: str,
    source_locator: Dict[str, Any],
    confidence: float,
    now: str,
    unit: str | None = None,
) -> Dict[str, Any]:
    return {
        "schemaVersion": "realytics-evidence-record-v1",
        "projectId": project_id,
        "recordType": "evidence",
        "recordId": f"evidence:auto_supplement:{_safe_key(canonical)}:{_hash(source_locator)}",
        "key": canonical,
        "evidenceType": evidence_type,
        "value": value,
        "unit": unit,
        "sourceType": "auto_evidence_supplement",
        "sourceLocator": source_locator,
        "confidence": confidence,
        "createdAt": now,
    }


def _assumption_candidate_record(
    project_id: str,
    canonical: str,
    value: Any,
    *,
    assumption_type: str,
    source_locator: Dict[str, Any],
    confidence: float,
    now: str,
    unit: str | None = None,
) -> Dict[str, Any]:
    return {
        "schemaVersion": "realytics-assumption-candidate-v1",
        "projectId": project_id,
        "recordType": "assumption",
        "recordId": f"assumption:auto_supplement:{_safe_key(canonical)}:{_hash(source_locator)}",
        "key": canonical,
        "assumptionId": f"assumption:auto_supplement:{_safe_key(canonical)}",
        "assumptionType": assumption_type,
        "approvalStatus": "candidate_needs_review",
        "reviewStatus": "needs_revision",
        "value": value,
        "unit": unit,
        "sourceType": "auto_evidence_supplement",
        "sourceLocator": source_locator,
        "confidence": confidence,
        "createdAt": now,
    }


def _supplement_risk_record(
    project_id: str,
    pod_key: str,
    risk_code: str,
    canonical: str,
    message: str,
    source_locator: Dict[str, Any],
    *,
    now: str,
) -> Dict[str, Any]:
    return {
        "schemaVersion": "realytics-risk-issue-v1",
        "projectId": project_id,
        "recordType": "risk_issue",
        "recordId": f"risk_issue:{risk_code}:{_safe_key(pod_key)}:{_safe_key(canonical)}",
        "key": f"auto_supplement.{pod_key}.{canonical}.{risk_code}",
        "riskCode": risk_code,
        "category": "data_quality",
        "severity": "medium",
        "message": message,
        "sourceType": "auto_evidence_supplement",
        "sourceLocator": source_locator,
        "podKey": pod_key,
        "blocking": False,
        "reviewStatus": "needs_revision",
        "createdAt": now,
    }


def _update_related_risks(
    project_id: str,
    *,
    pod_key: str,
    risk_code: str,
    canonical: str,
    supplement: Dict[str, Any],
    now: str,
) -> None:
    for risk in list_deal_state_records(project_id, record_type="risk_issue", limit=1000):
        if risk.get("podKey") != pod_key:
            continue
        if risk.get("riskCode") != risk_code:
            continue
        details = str((risk.get("sourceLocator") or {}).get("canonicalField") or risk.get("key") or "")
        if canonical and canonical not in details:
            continue
        updated = {
            **risk,
            "supplementStatus": supplement.get("status"),
            "supplementSource": supplement.get("source"),
            "supplementedAt": now,
            "autoSupplementRecordIds": [r.get("recordId") for r in supplement.get("records") or [] if r.get("recordId")],
        }
        upsert_deal_state_record(project_id, updated)


def _market_comparables(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    market = project_data.get("market") if isinstance(project_data.get("market"), dict) else {}
    records: List[Dict[str, Any]] = []
    for key in ("transactionComparables", "presaleComparables", "manualComparables", "listingComparables"):
        value = market.get(key)
        if isinstance(value, list):
            records.extend([item for item in value if isinstance(item, dict)])
    document_market = project_data.get("document_market_evidence")
    if isinstance(document_market, dict):
        for key in ("transactionComparables", "presaleComparables", "manualComparables", "listingComparables"):
            value = document_market.get(key)
            if isinstance(value, list):
                records.extend([item for item in value if isinstance(item, dict)])
    return records


def _fetch_and_inject_market_comparables(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    lawd = None
    land = project_data.get("land") if isinstance(project_data.get("land"), dict) else {}
    if land.get("pnu"):
        lawd = lawd_cd_from_pnu(land.get("pnu"))
    try:
        fetched, _diag = fetch_project_market_comparables(
            project_data,
            connector=MolitRtmsConnector(),
            deal_yms=recent_deal_yms(3),
            lawd_cd=lawd,
        )
    except Exception:
        return []
    if fetched:
        inject_market_comparables(project_data, fetched)
    return fetched


def _historical_candidate(project_id: str, canonical: str) -> Dict[str, Any] | None:
    # The compatibility snapshot catalog is global. Only normalized, tenant-
    # scoped sources may contribute candidates in the production runtime.
    if storage_backend() in {"postgres", "postgresql"}:
        return None
    seed_key = {
        "feasibility_input.total_revenue": "totalRevenue",
        "feasibility_input.total_cost": "totalCost",
        "feasibility_input.land_cost": "landCost",
        "pf_structure.amount.sum": "pfAmount",
        "pf.ltv": "ltv",
        "pf.exitSaleRate": "exitSaleRate",
    }.get(canonical)
    if not seed_key:
        return None
    state = load_state()
    snapshots = []
    for pid, rows in (state.get("project_snapshots") or {}).items():
        if str(pid) == str(project_id):
            continue
        if isinstance(rows, dict):
            snapshots.extend(rows.values())
    values: List[float] = []
    ids: List[str] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        seed = snapshot.get("caseComparisonSeed") or {}
        value = _to_float(seed.get(seed_key))
        if value is None:
            continue
        values.append(value)
        ids.append(str(snapshot.get("snapshotId") or "")[:80])
    if not values:
        return None
    return {
        "value": round(statistics.median(values), 4),
        "sampleSize": len(values),
        "snapshotIds": ids[:8],
        "confidence": min(0.72, 0.42 + min(len(values), 10) * 0.03),
    }


def _value_from_project(project_data: Dict[str, Any], canonical: str) -> Any:
    if canonical == "pf_structure.amount.sum":
        structure = project_data.get("pf_structure")
        if isinstance(structure, list):
            total = sum(_to_float(item.get("amount")) or 0 for item in structure if isinstance(item, dict))
            return total or None
    if canonical == "market.proposedPricePerPy":
        market = project_data.get("market") if isinstance(project_data.get("market"), dict) else {}
        return market.get("proposedPricePerPy") or market.get("proposed_price_per_py")
    path = canonical.split(".")
    current: Any = project_data
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _project_has_proposed_price(project_data: Dict[str, Any]) -> bool:
    return _value_from_project(project_data, "market.proposedPricePerPy") is not None


def _first_parcel_address(project_data: Dict[str, Any]) -> str:
    for source in (project_data.get("land_acquisition"), (project_data.get("landLedger") or {}).get("parcels")):
        if isinstance(source, list):
            for parcel in source:
                if isinstance(parcel, dict):
                    address = parcel.get("address") or parcel.get("addressFragment")
                    if address:
                        return str(address)
    return ""


def _supplemented_attempt(pod_key: str, canonical: str, source: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "podKey": pod_key,
        "canonicalField": canonical,
        "status": "supplemented",
        "source": source,
        "records": records,
    }


def _failed_attempt(pod_key: str, canonical: str, source: str, reason: str) -> Dict[str, Any]:
    return {
        "podKey": pod_key,
        "canonicalField": canonical,
        "status": "not_available",
        "source": source,
        "reason": reason,
        "records": [],
    }


def _attempt_public(attempt: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "podKey": attempt.get("podKey"),
        "canonicalField": attempt.get("canonicalField"),
        "status": attempt.get("status"),
        "source": attempt.get("source"),
        "reason": attempt.get("reason"),
        "createdRecordIds": [record.get("recordId") for record in attempt.get("records") or [] if record.get("recordId")],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("KRW", "").strip())
    except (TypeError, ValueError):
        return None


def _hash(value: Any) -> str:
    return hashlib.sha1(str(value).encode("utf-8", errors="ignore")).hexdigest()[:12]


def _safe_key(value: Any) -> str:
    return "".join(ch if ch.isalnum() or ch in "._:-" else "-" for ch in str(value or "none"))[:120] or "none"


def _unit_for(canonical: str) -> str | None:
    if canonical.endswith("pricePerSqm"):
        return "KRW/sqm"
    if canonical.endswith("pricePerPy") or canonical.endswith("PricePerPy"):
        return "KRW/py"
    if any(token in canonical for token in ("revenue", "cost", "amount", "waterfall")):
        return "KRW"
    if canonical.endswith("ltv") or canonical.endswith("exitSaleRate"):
        return "ratio"
    return None


def _term_for(canonical: str) -> str | None:
    return {
        "land.pnu": "pnu",
        "landEvidence.officialLandPrice.pricePerSqm": "official_land_price_per_sqm",
        "market.proposedPricePerPy": "sales_price_per_py",
        "marketComparable.pricePerPy": "sales_comparable_price_per_py",
        "feasibility_input.total_revenue": "total_sales_revenue",
        "feasibility_input.total_cost": "total_project_cost",
        "feasibility_input.construction_cost": "construction_cost",
        "feasibility_input.land_cost": "land_cost",
        "pf_structure.amount.sum": "pf_amount",
        "pf.ltv": "ltv",
        "pf.exitSaleRate": "exit_sale_rate",
    }.get(canonical)


def _critical_fields() -> set[str]:
    return {
        "land.pnu",
        "landEvidence.officialLandPrice.pricePerSqm",
        "market.proposedPricePerPy",
        "marketComparable.pricePerPy",
        "feasibility_input.total_revenue",
        "feasibility_input.total_cost",
        "feasibility_input.construction_cost",
        "pf_structure.amount.sum",
        "pf.ltv",
        "pf.exitSaleRate",
        "waterfall",
    }


def _locator_precision(locator: Dict[str, Any]) -> str:
    if locator.get("cell"):
        return "cell"
    if locator.get("apiRowId") or locator.get("pnu"):
        return "api_row"
    if locator.get("pageNumber") or locator.get("page"):
        return "page"
    return "source"
