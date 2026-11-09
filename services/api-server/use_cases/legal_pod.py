"""Independent Legal Pod execution and persistence contract.

This module keeps legal review deterministic and auditable. It does not issue a
legal opinion and it never invents registry, permit, or statutory evidence.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable
from uuid import uuid4

from agents.korean_law_context import ordinance_query_plan
from agents.legal_height_review import ordinance_jurisdiction
from agents.legal_permit_route import approval_document_route
from agents.legal_review_agent import LegalReviewAgent
# Deprecated compatibility symbol retained for integrations/tests that still
# patch it. Legal Pod no longer trusts or invokes this function directly.
from integrations.korean_law_mcp import run_korean_law_mcp  # noqa: F401
from integrations.land_data_connectors import LandDataConnectorRouter
from integrations.vworld import vworld_configured
from domains.feasibility.regulatory import build_preliminary_buildable_envelope
from use_cases.development_process import get_development_process
from use_cases.legal_intelligence import resolve_project_legal_context
from use_cases.legal_law_monitor import compare_law_references
from use_cases.land_legal_preview import PARCEL_BATCH_SIZE, ParcelPreviewBatch
from use_cases.parcel_pnu_resolution import (
    canonical_project_parcel_rows,
    parcel_lookup_address,
    parcel_pnu,
    resolve_parcel_rows,
)
from storage import (
    list_deal_state_records,
    list_land_evidence_records,
    list_reusable_public_land_evidence_records,
    public_land_evidence_payload_hash,
    storage_backend,
    upsert_deal_state_record,
    upsert_land_evidence_record,
    upsert_legal_review_record,
    upsert_permit_review_record,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _locator(value: Dict[str, Any], *, fallback: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Normalize document/page, Excel-cell, API-row and artifact locators."""
    raw = (
        value.get("evidenceLocator")
        or value.get("evidence_locator")
        or value.get("sourceLocator")
        or value.get("source_locator")
        or value.get("locator")
        or fallback
        or {}
    )
    if not isinstance(raw, dict):
        return {}
    return {
        key: raw[key]
        for key in (
            "sourceType", "sourceDocumentId", "evidenceId", "sourceUrl", "pageNumber",
            "sheetName", "cell", "rowNumber", "apiRowId", "artifactKey", "excerpt",
            "sourceAuthority", "transport", "resourceId", "resourceVersionId",
            "provisionId", "sourceFetchId", "officialId", "contentHash", "asOfDate",
            "effectiveDate", "fetchedAt", "verifiedAt", "verificationStatus",
        )
        if raw.get(key) not in (None, "", [], {})
    }


def _evidence_ids(value: Dict[str, Any]) -> list[str]:
    values = value.get("evidenceIds") or value.get("evidence_ids") or []
    return [str(item) for item in values if item not in (None, "")]


_PERMIT_DOCUMENT_TOKENS = {
    "permit",
    "approval",
    "authorization",
    "regulation",
    "zoning",
    "land_use",
    "building_code",
    "인허가",
    "허가",
    "승인",
    "용도지역",
    "토지이용",
}
_VERIFIED_DOCUMENT_STATUSES = {
    "approved",
    "human_reviewed",
    "schema_verified",
    "source_verified",
    "verified",
    "verified_snapshot",
}


def _verified_permit_document_evidence(
    parsed_documents: Any,
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    """Return only approved, source-located permit/regulation evidence."""

    if not isinstance(parsed_documents, list):
        return [], []
    verified: list[Dict[str, Any]] = []
    locators: list[Dict[str, Any]] = []
    for document in parsed_documents:
        if not isinstance(document, dict):
            continue
        classification = " ".join(
            str(
                document.get(key)
                or ""
            ).strip().casefold()
            for key in (
                "documentType",
                "document_type",
                "domain",
                "category",
                "taxonomyKey",
            )
        )
        if not approval_document_route(document) and not any(
            token in classification for token in _PERMIT_DOCUMENT_TOKENS
        ):
            continue
        verification_status = str(
            document.get("verificationStatus")
            or document.get("approvalStatus")
            or document.get("reviewStatus")
            or ""
        ).strip().casefold()
        if verification_status not in _VERIFIED_DOCUMENT_STATUSES:
            continue
        locator = _locator(document)
        if not locator:
            continue
        verified.append(document)
        locators.append(locator)
    return verified, locators


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "").replace("㎡", ""))
    except (TypeError, ValueError):
        return None


_PUBLIC_EVIDENCE_RESULT_KEYS = {
    "cadastral_characteristics": "cadastralCharacteristics",
    "land_use_planning": "landUsePlanningRecords",
    "building_use": "buildingUseRecords",
    "land_characteristics": "landCharacteristicsRecords",
    "official_land_price": "officialLandPrices",
}


def _cached_land_enrichment(records: list[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": "cache_hit" if records else "not_found",
        "pnuResolutions": [],
        "diagnostics": [],
    }
    for result_key in _PUBLIC_EVIDENCE_RESULT_KEYS.values():
        result[result_key] = []
    for record in records:
        result_key = _PUBLIC_EVIDENCE_RESULT_KEYS.get(str(record.get("evidenceType") or ""))
        if result_key:
            result[result_key].append(record)
    if records:
        result["diagnostics"].append({
            "connector": "public_pnu_evidence_cache",
            "status": "reused",
            "recordCount": len(records),
        })
    return result


def _merge_land_enrichment(cached: Dict[str, Any], live: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**cached, **live}
    for key in ["pnuResolutions", "diagnostics", *_PUBLIC_EVIDENCE_RESULT_KEYS.values()]:
        rows = []
        seen = set()
        for row in [*(cached.get(key) or []), *(live.get(key) or [])]:
            if not isinstance(row, dict):
                continue
            identity = (
                str(row.get("pnu") or row.get("regionCode") or ""),
                str(row.get("evidenceType") or row.get("sourceUrlOrEvidenceId") or row.get("connector") or ""),
                str(row.get("baseDate") or row.get("baseYear") or ""),
            )
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(row)
        merged[key] = rows
    merged["status"] = "fetched_with_cache" if cached.get("status") == "cache_hit" else live.get("status", "fetched")
    return merged


def _reusable_public_record(item: Dict[str, Any], evidence_type: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    reusable = {
        **item,
        "evidenceType": evidence_type,
        "sourceType": item.get("sourceType") or "official_public_api",
        "verificationStatus": item.get("verificationStatus") or "source_verified",
        "publicData": True,
        "reuseEligible": True,
        "cacheExpiresAt": item.get("cacheExpiresAt") or (now + timedelta(days=30)).isoformat(),
    }
    reusable["payloadHash"] = public_land_evidence_payload_hash(reusable)
    return reusable


def _parcel_source_rows(project_data: Dict[str, Any]) -> list[Dict[str, Any]]:
    return canonical_project_parcel_rows(project_data)


def _parcel_rows(parcels: list[Dict[str, Any]], persisted: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    by_pnu: Dict[str, list[Dict[str, Any]]] = {}
    for item in persisted:
        if item.get("pnu"):
            by_pnu.setdefault(str(item["pnu"]), []).append(item)
    rows: list[Dict[str, Any]] = []
    for index, parcel in enumerate(parcels):
        if not isinstance(parcel, dict):
            continue
        pnu = parcel_pnu(parcel)
        evidence = [*by_pnu.get(pnu, []), *(parcel.get("evidence") or [])]
        registry = [item for item in evidence if str(item.get("evidenceType") or item.get("type") or "").lower() in {"registry_record", "registry", "등기부"}]
        source_locators = [_locator(item, fallback={"sourceType": "land_evidence", "evidenceId": item.get("evidenceId")}) for item in evidence]
        source_locators = [item for item in source_locators if item]
        owner_status = parcel.get("ownerMatchStatus") or (registry[0].get("ownerMatchStatus") if registry else "not_verified")
        encumbrance = any(bool(item.get(key)) for item in evidence for key in (
            "mortgageExists", "seizureExists", "provisionalRegistrationExists", "easementExists", "trustRegistrationExists"
        )) or bool(parcel.get("mortgage") or parcel.get("encumbrance"))
        status = "needs_review" if not pnu or not registry or owner_status not in {"matched", "not_applicable"} or encumbrance else "reviewed"
        rows.append({
            "parcelIndex": index,
            "parcelKey": parcel.get("parcelKey") or pnu or f"parcel-{index + 1}",
            "pnu": pnu,
            "pnuResolutionStatus": "matched" if pnu else "unresolved",
            "pnuResolutionReason": (parcel.get("pnuResolution") or {}).get("reason") if isinstance(parcel.get("pnuResolution"), dict) else None,
            "status": status,
            "ownerMatchStatus": owner_status,
            "encumbrancePresent": encumbrance,
            "registryEvidenceCount": len(registry),
            "evidenceIds": [str(item.get("evidenceId")) for item in evidence if item.get("evidenceId")],
            "sourceLocators": source_locators,
        })
    return rows


def _check_risk(check: Dict[str, Any], locators: list[Dict[str, Any]], review_id: str) -> Dict[str, Any]:
    key = str(check.get("key") or "legal_review").upper()
    severity = str(check.get("severity") or "caution")
    issue_code = f"LEGAL_{key}"
    findings = check.get("findings") if isinstance(check.get("findings"), list) else []
    record = {
        "recordType": "risk_issue",
        "recordId": f"risk_issue:{issue_code}",
        "key": issue_code,
        "code": issue_code,
        "category": "legal",
        "severity": severity,
        "status": "open" if severity != "clear" else "resolved",
        "reviewStatus": "open" if severity != "clear" else "resolved",
        "blocking": severity == "blocking",
        "description": " / ".join(str(item) for item in findings) or str(check.get("title") or issue_code),
        "mitigation": "법무 증빙과 인허가·계약 조건을 확인한 후 승인 또는 조건부 승인 처리",
        "sourceType": "legal_evidence" if locators else "legal_review_record",
        "recordLocator": {
            "sourceType": "legal_review_record",
            "reviewId": review_id,
        },
        "evidenceIds": check.get("evidenceIds") or [],
        "legalReviewId": review_id,
    }
    if locators:
        record["evidenceLocator"] = locators[0]
        # Compatibility projection only. Approval uses evidenceLocator.
        record["sourceLocator"] = locators[0]
    return record


_LEGAL_CHECK_CONTEXT_TOKENS = {
    "zoning": {"zoning", "land_use", "far", "bcr", "용도지역", "용적률", "건폐율"},
    "permit": {"permit", "approval", "building", "인허가", "허가", "건축"},
    "land_rights": {"registry", "ownership", "land", "등기", "소유권", "토지"},
    "trust_security": {"trust", "security", "mortgage", "신탁", "담보", "저당"},
    "covenant": {"covenant", "loan", "finance", "약정", "대출", "금융"},
    "developability": {
        "developability",
        "zoning",
        "land_use",
        "building",
        "개발",
        "용도지역",
        "건축",
    },
}


def _legal_locators_for_check(
    check_key: str,
    legal_locators: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    if check_key in {"law_context", "law"}:
        return legal_locators
    tokens = _LEGAL_CHECK_CONTEXT_TOKENS.get(check_key, set())
    if not tokens:
        return []
    matched = []
    for locator in legal_locators:
        contexts = locator.get("claimContexts")
        contexts = contexts if isinstance(contexts, list) else []
        searchable = " ".join(
            str(context.get(field) or "").casefold()
            for context in contexts
            if isinstance(context, dict)
            for field in ("tool", "query", "purpose")
        )
        if any(token in searchable for token in tokens):
            matched.append(locator)
    return matched


def _legal_lifecycle_assessment(project_data: Dict[str, Any], review: Dict[str, Any], law_context: Dict[str, Any]) -> Dict[str, Any]:
    """Map legal review coverage onto the development lifecycle stages."""
    project = project_data.get("project") if isinstance(project_data.get("project"), dict) else {}
    asset_type = str(project.get("type") or project_data.get("assetType") or "mixed_use").lower()
    process = get_development_process(asset_type)
    references = law_context.get("references") or []
    review_status = review.get("status")
    stages = []
    for stage in [*(process.get("planningStages") or []), *(process.get("executionStages") or [])]:
        legal_relevant = "land_legal" in (stage.get("pods") or []) or "pf_credit" in (stage.get("pods") or [])
        if not legal_relevant:
            continue
        missing = []
        for evidence_key in stage.get("requiredEvidence") or []:
            if evidence_key in {"site_identity", "zoning"} and not (project_data.get("land") or project_data.get("regulation")):
                missing.append(evidence_key)
            elif evidence_key == "security_package" and not project_data.get("covenants"):
                missing.append(evidence_key)
        status = "needs_review" if review_status == "blocking_issue" or not references or missing else "reviewed"
        stages.append({
            "stageKey": stage.get("key"),
            "stageTitle": stage.get("title"),
            "status": status,
            "legalEvidenceCount": len(references),
            "missingEvidence": missing,
            "reviewChecks": [check.get("key") for check in review.get("checks") or [] if check.get("severity") in {"blocking", "caution"}],
        })
    return {
        "processVersion": process.get("version"),
        "assetType": asset_type,
        "stages": stages,
        "lawCoverageStatus": "connected" if references else "missing",
        "nextLegalStage": next((stage.get("stageKey") for stage in stages if stage.get("status") == "needs_review"), None),
    }


def _land_record_has_verified_source_pnu(record: Dict[str, Any]) -> bool:
    if record.get("sourcePnuVerified") is True:
        return True
    items = record.get("landUsePlanItems")
    return bool(record.get("pnu") and isinstance(items, list) and items) and all(
        isinstance(item, dict) and item.get("pnu") == record["pnu"]
        for item in items
    )


def _parcel_batch_result(
    batch: ParcelPreviewBatch,
    project_data: Dict[str, Any],
    parcels: list[Dict[str, Any]],
    records: list[Dict[str, Any]],
    land_use_by_pnu: Dict[str, Dict[str, Any]],
    enrichment: Dict[str, Any],
) -> Dict[str, Any]:
    land = project_data.get("land") or {}
    address = str(land.get("address") or "")
    diagnostics = enrichment.get("diagnostics") or []
    cadastral_addresses = {
        record["pnu"]: record["cadastralAddress"] for record in records
        if record.get("pnu") and record.get("cadastralAddress")
        and record.get("evidenceType") in {"cadastral", "cadastral_characteristics"}
    }
    verified_pnus = {
        str(record["pnu"]) for record in records
        if record.get("pnu") and (
            record.get("evidenceType") in {"cadastral", "cadastral_characteristics", "land_use_planning"}
            or (
                record.get("evidenceType") == "pnu_resolution"
                and record.get("verificationStatus") == "resolved"
                and record.get("matchMethod") in {"pnu_construction", "address_geocode"}
            )
        )
    }
    rows = []
    groups: dict[tuple[str, str | None], Dict[str, Any]] = {}
    for index in batch.indexes:
        parcel = parcels[index - 1]
        pnu = parcel_pnu(parcel)
        resolution = parcel.get("pnuResolution") or {}
        reason = resolution.get("reason") if isinstance(resolution, dict) else None
        confirmed = bool(pnu) and (
            pnu in verified_pnus or pnu in land_use_by_pnu
            or (
                isinstance(resolution, dict) and resolution.get("lotVerification") == "verified"
                and parcel.get("pnuMatchMethod") in {"pnu_construction", "address_geocode", "manual_pnu_exact"}
            )
        )
        if confirmed:
            status = "confirmed"
        elif reason == "conflicting_existing_pnu" or parcel.get("pnuCandidateCount", 0) not in (0, 1, None):
            status = "ambiguous"
        elif reason:
            status = "failed"
        else:
            status = "not_queried"

        planning = land_use_by_pnu.get(pnu) if confirmed else None
        planning_locator = _locator(planning, fallback={
            "sourceType": "land_use_planning_record",
            "sourceUrl": planning.get("sourceUrlOrEvidenceId"),
            "apiRowId": pnu,
        }) if planning else None
        planning_failure = next((
            item.get("status") for item in diagnostics
            if item.get("pnu") == pnu and item.get("connector") == "land_use_planning"
            and item.get("status") in {"fetch_failed", "pnu_unverified", "no_record"}
        ), None) if pnu else None
        zoning = planning.get("zoning") if planning else None
        plan = planning.get("districtUnitPlan") if planning else None
        zones = (planning.get("landUseZones") or []) if planning else []
        if not plan and isinstance(zones, list):
            plan = next((zone for zone in zones if isinstance(zone, str) and "지구단위계획" in zone), None)
        locators = list(parcel.get("parcelSourceLocators") or [])
        if locator := _locator(parcel):
            if locator not in locators:
                locators.append(locator)
        if confirmed:
            pnu_locator = {"sourceType": "api_row", "apiRowId": pnu}
            if pnu_locator not in locators:
                locators.append(pnu_locator)
        if planning_locator and planning_locator not in locators:
            locators.append(planning_locator)
        row = {
            "parcelIndex": index,
            **({"requestedPnu": parcel["requestedPnu"]} if parcel.get("requestedPnu") else {}),
            "parcelLabel": parcel.get("normalizedLotNumber") or parcel.get("lotNumber") or parcel.get("addressFragment") or parcel.get("requestedPnu"),
            "address": parcel_lookup_address(parcel, address) or parcel.get("addressFragment"),
            "pnu": pnu if confirmed else None,
            "status": status,
            "reason": reason or planning_failure,
            "landUseStatus": "confirmed" if planning else "failed" if planning_failure else "not_queried",
            "landUseSourceLocator": planning_locator,
            "zoning": zoning,
            "districtUnitPlan": plan,
            "sourceLocators": locators,
        }
        rows.append(row)
        if confirmed:
            jurisdiction = pnu[:5]
            jurisdiction_name = ordinance_jurisdiction({
                "land": {"address": cadastral_addresses.get(pnu) or (
                    resolution.get("resolvedAddress") if isinstance(resolution, dict) else None
                )},
                "land_acquisition": [{"pnu": pnu}],
            })
            group = groups.setdefault((jurisdiction, zoning), {
                "jurisdictionCode": jurisdiction,
                "jurisdictionName": jurisdiction_name,
                "zoning": zoning,
                "parcelIndexes": [],
                "districtUnitPlans": [],
                "sourceLocators": [],
            })
            if not group["jurisdictionName"] and jurisdiction_name:
                group["jurisdictionName"] = jurisdiction_name
            group["parcelIndexes"].append(index)
            if plan and plan not in group["districtUnitPlans"]:
                group["districtUnitPlans"].append(plan)
            if planning_locator and planning_locator not in group["sourceLocators"]:
                group["sourceLocators"].append(planning_locator)
    return {
        "parcelRevision": batch.revision,
        "totalParcels": batch.total,
        "nextOffset": batch.next_offset,
        "rows": rows,
        "jurisdictions": list(groups.values()),
    }


def run_legal_pod_use_case(
    project_id: str,
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
    *,
    persist: bool = True,
    parcel_batch: ParcelPreviewBatch | None = None,
    manual_pnu_preview: bool = False,
) -> Dict[str, Any]:
    """Run a Land-Legal review, optionally without any result persistence."""
    options = input_data or {}
    normalized_storage = storage_backend() == "postgres"
    land_enrichment = {"status": "not_requested", "diagnostics": []}
    # Chief's read-only collection projection already contains PNU-checked public
    # responses. Do not discard those in favour of the persisted-only query below.
    preview_pnus = {parcel_pnu(row) for row in _parcel_source_rows(project_data)}
    preview_pnus.add(parcel_pnu(project_data.get("land") or {}))
    live_preview_records = [
        deepcopy(row) for row in (project_data.get("landEvidence") or {}).get("records") or []
        if not persist and row.get("pnu") in preview_pnus and row.get("pnu")
        and row.get("sourcePnuVerified") is True and row.get("sourceLocator")
        and row.get("evidenceType") in {"cadastral", "land_use_planning", "official_land_price", "land_characteristics"}
    ]
    if options.get("autoEnrichLand", True):
        parcels = _parcel_source_rows(project_data)
        preview_resolution = None
        selected_indexes = set(parcel_batch.indexes) if parcel_batch else None
        if not persist and not manual_pnu_preview and vworld_configured() and any(
            not parcel_pnu(parcel)
            for index, parcel in enumerate(parcels, start=1)
            if selected_indexes is None or index in selected_indexes
        ):
            project_data = deepcopy(project_data)
            preview_resolution, _ = resolve_parcel_rows(
                project_data, str(project_id), limit=PARCEL_BATCH_SIZE, parcel_indexes=selected_indexes,
                timeout_seconds=2.0 if parcel_batch else 8.0,
            )
            parcels = _parcel_source_rows(project_data)
        if parcels:
            known_pnus = [pnu for parcel in parcels if (pnu := parcel_pnu(parcel))]
            cached_records = (
                []
                if options.get("forceRefreshLandEvidence") is True or (not persist and not normalized_storage)
                else list_reusable_public_land_evidence_records(known_pnus, project_id=str(project_id))
            )
            if parcel_batch:
                cached_records = [
                    record for record in cached_records
                    if record.get("evidenceType") not in {"cadastral", "cadastral_characteristics", "land_use_planning"}
                    or _land_record_has_verified_source_pnu(record)
                ]
            cached_types_by_pnu: Dict[str, set[str]] = {}
            for record in cached_records:
                cached_types_by_pnu.setdefault(str(record.get("pnu") or ""), set()).add(
                    str(record.get("evidenceType") or "")
                )
            minimum_reuse_types = {"land_use_planning", "official_land_price"}
            parcels_to_fetch = [
                {**parcel, "pnu": pnu}
                for index, parcel in enumerate(parcels, start=1)
                if (selected_indexes is None or index in selected_indexes)
                and (pnu := parcel_pnu(parcel))
                and not minimum_reuse_types.issubset(cached_types_by_pnu.get(pnu, set()))
            ]
            deferred_parcels = max(0, len(parcels_to_fetch) - PARCEL_BATCH_SIZE) if not persist else 0
            if not persist:
                parcels_to_fetch = parcels_to_fetch[:PARCEL_BATCH_SIZE]
            cached_enrichment = _cached_land_enrichment(cached_records)
            if parcels_to_fetch:
                live_enrichment = LandDataConnectorRouter().fetch_records_for_parcels(
                    parcels_to_fetch,
                    base_year=options.get("officialLandPriceBaseYear"),
                    **({} if persist else {"timeout_seconds": 2.0, "include_region_trends": False}),
                )
                land_enrichment = _merge_land_enrichment(cached_enrichment, live_enrichment)
            else:
                land_enrichment = cached_enrichment
            if preview_resolution is not None:
                land_enrichment["previewParcelResolution"] = preview_resolution
            if deferred_parcels:
                land_enrichment["status"] = "partial"
                land_enrichment.setdefault("diagnostics", []).append({
                    "connector": "land_use_planning",
                    "status": "skipped_preview_limit",
                    "message": f"한 번의 미리보기에서 조회할 필지 수를 제한해 {deferred_parcels}필지의 공공자료 확인이 남았습니다.",
                })
            missing_parcels = [parcel for parcel in parcels if not parcel_pnu(parcel)]
            if missing_parcels:
                land_enrichment["status"] = "partial"
                land_enrichment.setdefault("diagnostics", []).extend({
                    "connector": "pnu",
                    "status": "skipped_missing_pnu",
                    "message": "필지별 지번과 법정동을 확인한 뒤 PNU 조회를 실행하세요.",
                    "parcelKey": parcel.get("parcelKey") or parcel.get("addressFragment") or parcel.get("lotNumber"),
                } for parcel in missing_parcels)
            land_enrichment["publicEvidenceCache"] = {
                "requestedPnuCount": len(set(known_pnus)),
                "reusedRecordCount": len(cached_records),
                "externalFetchParcelCount": len(parcels_to_fetch),
                "reuseScope": "current_project_official_facts_only" if normalized_storage else "official_public_facts_only",
            }
            for evidence_type, result_key in _PUBLIC_EVIDENCE_RESULT_KEYS.items():
                for item in land_enrichment.get(result_key, []):
                    if item.get("pnu") not in known_pnus:
                        continue
                    pnu = item["pnu"]
                    record = {
                        **item,
                        "evidenceType": evidence_type,
                        "recordKey": f"{evidence_type}:{pnu}",
                        "sourceLocator": item.get("sourceLocator") or {
                            "sourceType": "api_row",
                            "sourceUrl": item.get("sourceUrlOrEvidenceId"),
                            "apiRowId": pnu,
                        },
                    }
                    if persist:
                        upsert_land_evidence_record(str(project_id), _reusable_public_record(record, evidence_type))
                    else:
                        live_preview_records.append(record)
            if persist:
                for item in land_enrichment.get("regionLandPriceTrends", []):
                    region_key = item.get("regionCode") or item.get("cityCountyCode") or "unknown"
                    upsert_land_evidence_record(str(project_id), {
                        **item,
                        "evidenceType": "land_price_trend",
                        "recordKey": f"land_price_trend:{region_key}:{item.get('standardYear')}:{item.get('standardMonth') or 'annual'}",
                        "sourceLocator": {
                            "sourceType": "api_row",
                            "sourceUrl": item.get("sourceUrlOrEvidenceId"),
                            "apiRowId": f"{region_key}:{item.get('standardYear')}:{item.get('standardMonth') or 'annual'}",
                        },
                    })
    # The review projection must not mutate canonical project dictionaries.
    # In particular, parcel API enrichment must never overwrite top-level
    # regulation state through a shallow copy.
    review_data = deepcopy(project_data)
    for field in ("regulation", "covenants", "landEvidence"):
        if not isinstance(review_data.get(field), dict):
            review_data[field] = {}
    review_parcels = _parcel_source_rows(review_data)
    for parcel in review_parcels:
        pnu = parcel_pnu(parcel)
        if pnu:
            parcel["pnu"] = pnu
        else:
            # Keep unresolved identifiers from becoming an FAR/BCR capacity
            # input. The stored ledger and its original source remain intact.
            for key in ("pnu", "resolvedPnu", "PNU"):
                parcel.pop(key, None)
    review_data["land_acquisition"] = review_parcels
    parcel_pnus = {parcel_pnu(parcel) for parcel in review_parcels} - {""}
    if not review_parcels and isinstance(review_data.get("land"), dict):
        parcel_pnus.update({parcel_pnu(review_data["land"])} - {""})
    persisted_land_records = [
        record for record in list_land_evidence_records(str(project_id))
        if record.get("pnu") in parcel_pnus or (not manual_pnu_preview and not record.get("pnu"))
    ]
    if parcel_batch:
        persisted_land_records = [
            record for record in persisted_land_records
            if record.get("evidenceType") not in {"cadastral", "cadastral_characteristics", "land_use_planning"}
            or _land_record_has_verified_source_pnu(record)
        ]
    review_data["landEvidence"]["records"] = [*live_preview_records, *persisted_land_records]
    review_land_records = [*live_preview_records, *persisted_land_records]
    cadastral = [item for item in review_land_records if item.get("evidenceType") in {"cadastral_characteristics", "cadastral"}]
    current_land_use = [
        record for record in land_enrichment.get("landUsePlanningRecords") or []
        if record.get("pnu") in parcel_pnus
    ]
    persisted_land_use = [item for item in review_land_records if item.get("evidenceType") == "land_use_planning"]
    land_use_records = [*current_land_use, *persisted_land_use]
    land_use_by_pnu: Dict[str, Dict[str, Any]] = {}
    for item in reversed(land_use_records):
        pnu = str(item.get("pnu") or "").strip()
        if pnu:
            land_use_by_pnu[pnu] = item
    for parcel in review_parcels:
        pnu = parcel_pnu(parcel)
        record = land_use_by_pnu.get(pnu)
        if not record:
            continue
        # Project the matched evidence as one unit. A value from an older
        # project input cannot inherit the new record's locator.
        parcel_land_use = {
            key: record.get(key)
            for key in ("legalFar", "legalBcr", "zoning", "districtUnitPlan", "landUsePlanItems")
        }
        for key in ("legalFar", "legalBcr", "legal_far", "legal_bcr", "land_use_planning"):
            parcel.pop(key, None)
        zones = record.get("landUseZones") or ([record.get("zoning")] if record.get("zoning") else [])
        parcel_land_use["landUseZones"] = list(dict.fromkeys(str(zone) for zone in zones if zone))
        parcel_land_use["sourceLocator"] = record.get("sourceLocator") or {
            "sourceType": "land_use_planning_record",
            "sourceUrl": record.get("sourceUrlOrEvidenceId"),
            "evidenceId": record.get("evidenceId"),
            "apiRowId": pnu,
        }
        parcel["landUsePlanning"] = parcel_land_use
    if not review_data["regulation"].get("zoning"):
        zones = []
        for record in [*land_use_records, *cadastral]:
            for zone in record.get("landUseZones") or ([record.get("zoning")] if record.get("zoning") else []):
                if zone and str(zone) not in zones:
                    zones.append(str(zone))
        if zones:
            review_data["regulation"]["zoning"] = zones[0] if len(zones) == 1 else "multi_parcel"
            review_data["regulation"]["landUseZones"] = zones
    if not isinstance(review_data.get("land"), dict):
        review_data["land"] = {}
    land_ledger = review_data.get("landLedger") or review_data.get("land_ledger") or {}
    land_ledger = land_ledger if isinstance(land_ledger, dict) else {}
    review_data["landLedger"] = {**land_ledger, "parcels": review_parcels}
    land_summary = land_ledger.get("summary") if isinstance(land_ledger.get("summary"), dict) else {}
    acquisition_status = land_ledger.get("landAcquisitionStatus") if isinstance(land_ledger.get("landAcquisitionStatus"), dict) else {}
    if not review_data["land"].get("area_sqm"):
        ledger_area = _number(land_summary.get("totalAreaSqm") or acquisition_status.get("privateLandArea"))
        if ledger_area:
            review_data["land"]["area_sqm"] = ledger_area
            review_data["land"]["areaSqm"] = ledger_area
            review_data["land"]["sourceLocator"] = {"sourceType": "excel_cell", "artifactKey": "landLedger.summary.totalAreaSqm"}
    if acquisition_status:
        review_data["land"]["acquisitionAreaRatio"] = acquisition_status.get("acquisitionAreaRatio")
    if not review_data["land"].get("area_sqm") and cadastral and len(parcel_pnus) >= len(review_parcels):
        area_by_pnu = {item.get("pnu"): _number(item.get("areaSqm")) for item in reversed(cadastral)}
        if parcel_pnus and all((area_by_pnu.get(pnu) or 0) > 0 for pnu in parcel_pnus):
            review_data["land"]["area_sqm"] = sum(area_by_pnu[pnu] for pnu in parcel_pnus)
    buildable_envelope = build_preliminary_buildable_envelope(
        review_data,
        declared_land_area_sqm=review_data["land"].get("area_sqm"),
    )
    if buildable_envelope:
        review_data["buildableEnvelope"] = buildable_envelope
        review_data["regulation"]["buildableEnvelope"] = buildable_envelope
        if buildable_envelope.get("effectiveLegalFar") is not None:
            effective_far = buildable_envelope["effectiveLegalFar"]
            review_data["regulation"]["legal_far"] = effective_far
            review_data["regulation"]["legalFar"] = effective_far
            review_data["regulation"]["legalFarBasis"] = "parcel_weighted_envelope"
        if buildable_envelope.get("effectiveLegalBcr") is not None:
            effective_bcr = buildable_envelope["effectiveLegalBcr"]
            review_data["regulation"]["legal_bcr"] = effective_bcr
            review_data["regulation"]["legalBcr"] = effective_bcr
            review_data["regulation"]["legalBcrBasis"] = "parcel_weighted_envelope"
    verified_permit_documents, permit_locators = _verified_permit_document_evidence(
        options.get("parsedDocuments")
    )
    batch_result = (
        _parcel_batch_result(
            parcel_batch, review_data, review_parcels, review_land_records,
            land_use_by_pnu, land_enrichment,
        )
        if parcel_batch is not None else None
    )
    parcel_jurisdictions = tuple(dict.fromkeys(
        group["jurisdictionName"] for group in batch_result["jurisdictions"] if group["jurisdictionName"]
    )) if batch_result else ()
    supplied_law_context = options.get("lawContext") or options.get("law_context")
    legal_resolution = resolve_project_legal_context(
        review_data,
        caller_context=supplied_law_context if isinstance(supplied_law_context, dict) else None,
        **({} if persist else {"preview": True}),
        **({"parcel_jurisdictions": parcel_jurisdictions} if parcel_jurisdictions else {}),
    )
    # Caller-provided context and MCP search hits stay discovery candidates.
    law_context = legal_resolution["lawContext"]
    legal_intelligence = legal_resolution["legalIntelligence"]
    approval_documents = []
    for document, locator in zip(verified_permit_documents, permit_locators):
        route = approval_document_route(document)
        page = locator.get("pageNumber")
        has_exact_location = (
            type(page) is int and page > 0
            or bool(locator.get("sheetName") and locator.get("cell"))
        )
        if route and locator.get("sourceDocumentId") and has_exact_location:
            approval_documents.append({**locator, "route": route})
    review = LegalReviewAgent().review(
        review_data, law_context, approval_documents=approval_documents,
    )
    # Preserve the source attestation fields that the legacy display
    # normalizer does not know about.
    review["lawReferences"] = list(legal_intelligence.get("references") or [])
    review["citationVerification"] = legal_intelligence.get(
        "citationVerification"
    )
    lifecycle_assessment = _legal_lifecycle_assessment(review_data, review, law_context)
    previous_law_records = [
        record for record in list_deal_state_records(str(project_id))
        if record.get("recordType") == "legal_evidence"
    ]
    law_change_report = compare_law_references(previous_law_records, review.get("lawReferences") or [])
    review_id = str(options.get("reviewId") or f"legal-review:{project_id}:{uuid4()}")
    parcels = _parcel_rows(review_parcels, review_land_records)
    parcel_blockers = [
        row for row in parcels
        if row["status"] == "needs_review"
        and (row.get("encumbrancePresent") or row.get("ownerMatchStatus") not in {None, "not_verified", "matched", "not_applicable"})
    ]
    parcel_pending = [row for row in parcels if row["status"] == "needs_review" and row not in parcel_blockers]
    parcel_locators = [
        locator
        for row in parcels
        for locator in row.get("sourceLocators", [])
    ]
    legal_reference_locators = [
        {
            "sourceType": "api_row",
            "sourceUrl": reference.get("url") or None,
            "apiRowId": reference.get("lawId"),
            "sourceAuthority": reference.get("sourceAuthority") or "unknown",
            "transport": reference.get("transport") or "unknown",
            "resourceId": reference.get("resourceId"),
            "resourceVersionId": reference.get("resourceVersionId"),
            "provisionId": (
                reference.get("provisionId")
                or reference.get("legalProvisionId")
            ),
            "sourceFetchId": reference.get("sourceFetchId"),
            "officialId": reference.get("lawId"),
            "contentHash": reference.get("contentHash"),
            "asOfDate": reference.get("asOfDate"),
            "effectiveDate": reference.get("effectiveDate"),
            "fetchedAt": reference.get("fetchedAt"),
            "verifiedAt": reference.get("verifiedAt"),
            "verificationStatus": reference.get("verificationStatus")
            or "not_verified",
            "claimContexts": reference.get("claimContexts") or [],
        }
        for reference in legal_intelligence.get("references") or []
    ]
    legal_reference_locators = [
        {key: value for key, value in locator.items() if value not in (None, "")}
        for locator in legal_reference_locators
    ]
    locators = [*parcel_locators, *legal_reference_locators]
    statutory_locator_complete = bool(legal_reference_locators) and all(
        locator.get("officialId")
        and locator.get("contentHash")
        and locator.get("asOfDate")
        and locator.get("verificationStatus")
        in {"verified_snapshot", "human_reviewed"}
        and locator.get("provisionId")
        and locator.get("sourceFetchId")
        and (
            locator.get("sourceUrl")
            or locator.get("resourceVersionId")
        )
        for locator in legal_reference_locators
    )

    supplied_permit_documents = (
        options.get("parsedDocuments")
        if isinstance(options.get("parsedDocuments"), list)
        else []
    )
    locators = [*locators, *permit_locators]
    checks = []
    for check in review.get("checks", []):
        check_key = str(check.get("key") or "review").casefold()
        check_locators = [
            *(
                parcel_locators
                if check_key == "land_rights"
                else []
            ),
            *(
                permit_locators
                if check_key == "permit"
                else []
            ),
            *_legal_locators_for_check(
                check_key,
                legal_reference_locators,
            ),
        ]
        enriched = {
            **check,
            "checkCode": f"LEGAL_{str(check.get('key') or 'REVIEW').upper()}",
            "responsibleAgent": "LegalReviewAgent",
            "evidenceLocators": check_locators[:10],
            "sourceLocators": check_locators[:10],
            "evidenceIds": [
                item.get("evidenceId")
                for item in check_locators
                if item.get("evidenceId")
            ],
        }
        checks.append(enriched)

    status = "blocking_issue" if review.get("status") == "blocking_issue" or parcel_blockers else "caution" if review.get("status") == "caution" or parcel_pending else "clear"
    risk_issues = [
        _check_risk(
            check,
            list(check.get("sourceLocators") or []),
            review_id,
        )
        for check in checks
        if check.get("severity") in {"blocking", "caution"}
    ]
    if parcel_pending:
        risk_issues.append({
            "recordType": "risk_issue", "recordId": "risk_issue:LEGAL_PARCEL_REGISTRY_EVIDENCE",
            "key": "LEGAL_PARCEL_REGISTRY_EVIDENCE", "code": "LEGAL_PARCEL_REGISTRY_EVIDENCE",
            "category": "legal", "severity": "caution", "status": "open", "reviewStatus": "open", "blocking": False,
            "description": f"{len(parcel_blockers)}개 필지의 등기·제한물권 증빙 또는 소유자 일치 확인이 필요합니다.",
            "mitigation": "필지별 등기부 증빙과 제한물권 말소 조건을 첨부하고 법무 검토를 완료합니다.",
            "sourceType": "land_evidence",
            "recordLocator": {
                "sourceType": "legal_review_record",
                "reviewId": review_id,
            },
            "evidenceIds": [
                item.get("evidenceId")
                for item in locators
                if item.get("evidenceId")
            ],
            "legalReviewId": review_id,
        })
        if parcel_locators:
            risk_issues[-1]["evidenceLocator"] = parcel_locators[0]
            risk_issues[-1]["sourceLocator"] = parcel_locators[0]
        risk_issues[-1]["description"] = f"{len(parcel_pending)}필지의 등기·제한물권 증빙이 아직 첨부되지 않았습니다."
        risk_issues[-1]["mitigation"] = "등기부 발급본과 제한물권 확인자료를 첨부한 뒤 법무 Pod를 재실행합니다."

    developability = review.get("developabilityAnalysis") or {}
    if batch_result:
        permit_routes = (developability.get("permitRouteReview") or {}).get("routes") or []
        for group in batch_result["jurisdictions"]:
            queries = {item["query"] for item in ordinance_query_plan(group["jurisdictionName"])} if group["jurisdictionName"] else set()
            group["ordinanceReferences"] = [
                reference for reference in review["lawReferences"]
                if reference.get("verificationStatus") in {"verified_snapshot", "human_reviewed"}
                and any(
                    context.get("tool") == "search_ordinance" and context.get("query") in queries
                    for context in reference.get("claimContexts") or []
                )
            ]
            group["permitRoutes"] = permit_routes
    missing_land_inputs = list(developability.get("missingRequiredInputs") or [])
    land_pod_handoff = {
        "handoffType": "land_to_legal_context",
        "fromPod": "land_legal",
        "fromAgent": "LandAgent",
        "toPod": "land_legal",
        "toAgent": "LegalReviewAgent",
        "status": "needs_review" if missing_land_inputs else "accepted",
        "sourceArtifact": "landLedger/landAgentReview",
        "dataUsed": [
            "landLedger.summary.totalAreaSqm",
            "landLedger.landAcquisitionStatus.acquisitionAreaRatio",
            "landLedger.parcels[].pnu",
            "landEnrichment.pnuResolutions",
            "landEnrichment.landUsePlanningRecords",
        ],
        "missingFields": missing_land_inputs,
        "requestedFields": [
            "regulation.zoning",
            "regulation.legal_far",
            "regulation.legal_bcr",
            "regulation.districtUnitPlan",
        ] if missing_land_inputs else [],
        "nextAction": "Run Land Pod enrichment from VWorld/public land-use sources before final developability conclusion." if missing_land_inputs else "Land context accepted by Legal Review Agent.",
    }

    legal_record = {
        **review,
        "reviewId": review_id,
        "status": status,
        "reviewLevel": "preliminary",
        "checks": checks,
        "parcelLegalReviews": parcels,
        "evidenceLocators": locators,
        "sourceLocatorStatus": (
            "complete"
            if statutory_locator_complete
            else "weak"
            if locators
            else "missing"
        ),
        "analysisOnly": True,
        "reportEligible": False,
        "humanReviewStatus": "pending",
        "reviewStatus": "pending",
        "legalIntelligence": legal_intelligence,
        "mcp": {
            **review.get("mcp", {}),
            "connected": law_context.get("connected", False),
            "status": "connected" if law_context.get("connected") else "not_connected",
            "verifiedEvidenceAvailable": law_context.get(
                "verifiedEvidenceAvailable",
                False,
            ),
            "failureReason": (
                None
                if law_context.get("verifiedEvidenceAvailable")
                else "No current, hash-bound official legal evidence was verified."
            ),
            "citationVerification": law_context.get("citationVerification"),
            "transport": "provider_orchestration",
            "diagnostics": [
                diagnostic
                for provider in legal_intelligence.get("providerTrace") or []
                for diagnostic in provider.get("diagnostics") or []
            ],
            "compatibilityOnly": True,
        },
        "riskIssues": risk_issues,
        "landEnrichment": land_enrichment,
        "legalLifecycleAssessment": lifecycle_assessment,
        "lawChangeReport": law_change_report,
        "landPodHandoff": land_pod_handoff,
        "buildableEnvelope": buildable_envelope,
        "updatedAt": _now(),
    }
    permit_check = next(
        (item for item in checks if item.get("key") == "permit"),
        {},
    )
    permit_parser_status = (
        "verified"
        if verified_permit_documents
        else "unverified"
        if supplied_permit_documents
        else "not_available"
    )
    permit_record = {
        "reviewId": f"permit-review:{project_id}:{uuid4()}",
        "status": (
            str(permit_check.get("severity") or "caution")
            if verified_permit_documents
            else "pending_evidence"
        ),
        "reviewLevel": "preliminary",
        "permitCheck": permit_check,
        "permitRouteReview": review["developabilityAnalysis"]["permitRouteReview"],
        "sourceLocators": permit_locators,
        "parserStatus": permit_parser_status,
        "evidenceStatus": (
            "verified" if verified_permit_documents else "missing"
        ),
        "verifiedEvidenceCount": len(verified_permit_documents),
        "storedDocumentCount": len(supplied_permit_documents),
        "documentEvidenceStages": [
            {"sourceDocumentId": item.get("archiveId") or item.get("originalFileHash"),
             "stage": item.get("evidenceStage") or "candidate"}
            for item in supplied_permit_documents
        ],
        "parserFailureReason": (
            None
            if verified_permit_documents
            else (
                "Provided permit/regulation parser output is not approved or "
                "does not have an exact source locator."
                if supplied_permit_documents
                else "No permit, regulation, or contract document was provided."
            )
        ),
        "analysisOnly": True,
        "reportEligible": False,
        "updatedAt": _now(),
    }
    if persist and not normalized_storage:
        upsert_permit_review_record(str(project_id), permit_record)
    for index, reference in enumerate(review.get("lawReferences") or []):
        reference_id = str(reference.get("lawId") or reference.get("lawName") or reference.get("title") or f"reference-{index}")
        law_record = {
            "recordType": "legal_evidence",
            "recordId": f"legal_evidence:{project_id}:{review_id}:{index}",
            "key": reference_id,
            "valueType": "source",
            "lawReference": reference,
            "sourceType": reference.get("sourceAuthority") or "official_legal_source",
            "evidenceLocator": {
                "sourceType": "api_row",
                "sourceUrl": reference.get("url"),
                "apiRowId": reference.get("lawId") or reference_id,
                "article": reference.get("article"),
                "effectiveDate": reference.get("effectiveDate"),
                "sourceAuthority": reference.get("sourceAuthority"),
                "transport": reference.get("transport"),
                "resourceId": reference.get("resourceId"),
                "resourceVersionId": reference.get("resourceVersionId"),
                "officialId": reference.get("lawId"),
                "contentHash": reference.get("contentHash"),
                "asOfDate": reference.get("asOfDate"),
                "fetchedAt": reference.get("fetchedAt"),
                "verifiedAt": reference.get("verifiedAt"),
                "verificationStatus": reference.get("verificationStatus"),
            },
            "evidenceIds": [reference.get("lawId") or reference_id],
            "legalReviewId": review_id,
            "createdAt": _now(),
        }
        # Read-only compatibility alias for consumers predating the explicit
        # recordLocator/evidenceLocator contract.
        law_record["sourceLocator"] = law_record["evidenceLocator"]
        if persist:
            upsert_deal_state_record(str(project_id), law_record)
    reference_by_key = {
        str(
            reference.get("lawId")
            or reference.get("lawName")
            or reference.get("title")
            or ""
        ): reference
        for reference in review.get("lawReferences") or []
    }
    for change_type in ("added", "removed", "changed"):
        for index, change in enumerate(law_change_report.get(change_type) or []):
            change_reference = reference_by_key.get(str(change.get("lawKey") or ""))
            change_record = {
                "recordType": "legal_law_change",
                "recordId": f"legal_law_change:{project_id}:{review_id}:{change_type}:{index}",
                "key": change.get("lawKey"),
                "changeType": change_type,
                "lawChange": change,
                "sourceType": (
                    (change_reference or {}).get("sourceAuthority")
                    or "legal_change_analysis"
                ),
                "evidenceLocator": {
                    key: value
                    for key, value in {
                        "sourceType": "api_row",
                        "apiRowId": change.get("lawKey"),
                        "sourceUrl": (change_reference or {}).get("url"),
                        "sourceAuthority": (change_reference or {}).get("sourceAuthority"),
                        "transport": (change_reference or {}).get("transport"),
                        "officialId": (change_reference or {}).get("lawId"),
                        "contentHash": (change_reference or {}).get("contentHash"),
                        "asOfDate": (change_reference or {}).get("asOfDate"),
                        "verificationStatus": (change_reference or {}).get("verificationStatus"),
                    }.items()
                    if value not in (None, "")
                },
                "legalReviewId": review_id,
                "status": "needs_review",
                "createdAt": _now(),
            }
            change_record["sourceLocator"] = change_record[
                "evidenceLocator"
            ]
            if persist:
                upsert_deal_state_record(str(project_id), change_record)
    if persist:
        for risk in risk_issues:
            upsert_deal_state_record(str(project_id), risk)
    handoff = {
        "handoffId": f"handoff:legal-pod:pf-credit:{project_id}:{review_id}",
        "fromAgent": "LegalReviewAgent",
        "fromPod": "land_legal",
        "toAgent": "PF-Credit Pod",
        "toPod": "pf_credit",
        "handoffType": "legal_readiness_review",
        "payloadSchema": "LegalReadinessPatchV1",
        "payload": {"legalReviewId": review_id, "status": status, "blockingRiskCodes": [item["code"] for item in risk_issues if item.get("blocking")]},
        "evidenceIds": [item.get("evidenceId") for item in locators if item.get("evidenceId")],
        "evidenceLocators": locators,
        "sourceLocators": locators,
        "requiresHumanReview": True,
        "reviewStatus": "pending",
        "analysisOnly": True,
        "reportEligible": False,
        "createdAt": _now(),
    }
    handoff_record = {
        "recordType": "handoff", "recordId": handoff["handoffId"], "key": handoff["handoffId"],
        "sourceType": "legal_pod",
        "recordLocator": {
            "sourceType": "legal_review_record",
            "reviewId": review_id,
        },
        **handoff,
    }
    if locators:
        handoff_record["evidenceLocator"] = locators[0]
        handoff_record["sourceLocator"] = locators[0]
    if persist:
        upsert_deal_state_record(str(project_id), handoff_record)
    legal_record["pfHandoff"] = handoff
    legal_record["nextActions"] = review.get("requiredFollowUps", [])
    if persist and not normalized_storage:
        upsert_legal_review_record(str(project_id), legal_record)
    return {
        "previewOnly": not persist,
        **({"parcelBatch": batch_result} if batch_result is not None else {}),
        "legalReview": legal_record,
        "permitReview": permit_record,
        "riskIssues": risk_issues,
        "handoff": handoff,
        "buildableEnvelope": buildable_envelope,
    }
