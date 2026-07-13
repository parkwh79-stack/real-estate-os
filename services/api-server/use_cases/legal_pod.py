"""Independent Legal Pod execution and persistence contract.

This module keeps legal review deterministic and auditable. It does not issue a
legal opinion and it never invents registry, permit, or statutory evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable
from uuid import uuid4

from agents.legal_review_agent import LegalReviewAgent
from agents.korean_law_context import normalize_law_context
from integrations.korean_law_mcp import run_korean_law_mcp
from integrations.land_data_connectors import LandDataConnectorRouter
from use_cases.development_process import get_development_process
from use_cases.legal_law_monitor import compare_law_references
from storage import (
    list_deal_state_records,
    list_land_evidence_records,
    upsert_deal_state_record,
    upsert_legal_review_record,
    upsert_permit_review_record,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _locator(value: Dict[str, Any], *, fallback: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Normalize document/page, Excel-cell, API-row and artifact locators."""
    raw = value.get("sourceLocator") or value.get("source_locator") or value.get("locator") or fallback or {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: raw[key]
        for key in (
            "sourceType", "sourceDocumentId", "evidenceId", "sourceUrl", "pageNumber",
            "sheetName", "cell", "rowNumber", "apiRowId", "artifactKey", "excerpt",
        )
        if raw.get(key) not in (None, "", [], {})
    }


def _evidence_ids(value: Dict[str, Any]) -> list[str]:
    values = value.get("evidenceIds") or value.get("evidence_ids") or []
    return [str(item) for item in values if item not in (None, "")]


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "").replace("㎡", ""))
    except (TypeError, ValueError):
        return None


def _parcel_source_rows(project_data: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Return the canonical parcel rows regardless of legacy container shape."""
    land = project_data.get("land") if isinstance(project_data.get("land"), dict) else {}
    candidates = (
        project_data.get("land_acquisition"),
        project_data.get("landLedger"),
        land.get("parcels"),
        land.get("landLedger"),
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if isinstance(candidate, dict):
            for key in ("parcels", "rows", "landLedger", "items"):
                rows = candidate.get(key)
                if isinstance(rows, list):
                    return [item for item in rows if isinstance(item, dict)]
    return []


def _parcel_rows(project_id: str, project_data: Dict[str, Any]) -> list[Dict[str, Any]]:
    parcels = _parcel_source_rows(project_data)
    persisted = list_land_evidence_records(str(project_id))
    by_pnu: Dict[str, list[Dict[str, Any]]] = {}
    for item in persisted:
        if item.get("pnu"):
            by_pnu.setdefault(str(item["pnu"]), []).append(item)
    rows: list[Dict[str, Any]] = []
    for index, parcel in enumerate(parcels):
        if not isinstance(parcel, dict):
            continue
        pnu = str(parcel.get("pnu") or parcel.get("PNU") or parcel.get("parcelKey") or "")
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
    return {
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
        "sourceType": "legal_review",
        "sourceLocator": locators[0] if locators else {"sourceType": "legal_review", "reviewId": review_id},
        "evidenceIds": check.get("evidenceIds") or [],
        "legalReviewId": review_id,
    }


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


def run_legal_pod_use_case(project_id: str, project_data: Dict[str, Any], input_data: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Run, persist, and return a Land-Legal review suitable for PF handoff."""
    options = input_data or {}
    land_enrichment = {"status": "not_requested", "diagnostics": []}
    if options.get("autoEnrichLand", True):
        land = project_data.get("land") if isinstance(project_data.get("land"), dict) else {}
        parcels = _parcel_source_rows(project_data)
        if parcels:
            supplied_pnu = str(land.get("pnu") or "")
            fallback_legal_dong_code = supplied_pnu[:10] if len(supplied_pnu) == 19 and supplied_pnu[:10].isdigit() else None
            land_enrichment = LandDataConnectorRouter().fetch_records_for_parcels(
            parcels,
                legal_dong_code=options.get("legalDongCode") or land.get("legalDongCode") or fallback_legal_dong_code,
                base_year=options.get("officialLandPriceBaseYear"),
            )
            for item in [
                *land_enrichment.get("cadastralCharacteristics", []),
                *land_enrichment.get("landUsePlanningRecords", []),
                *land_enrichment.get("buildingUseRecords", []),
                *land_enrichment.get("landCharacteristicsRecords", []),
                *land_enrichment.get("officialLandPrices", []),
            ]:
                if item.get("pnu"):
                    if item in land_enrichment.get("cadastralCharacteristics", []):
                        evidence_type = "cadastral_characteristics"
                    elif item in land_enrichment.get("landUsePlanningRecords", []):
                        evidence_type = "land_use_planning"
                    elif item in land_enrichment.get("buildingUseRecords", []):
                        evidence_type = "building_use"
                    elif item in land_enrichment.get("landCharacteristicsRecords", []):
                        evidence_type = "land_characteristics"
                    else:
                        evidence_type = "official_land_price"
                    from storage import upsert_land_evidence_record
                    upsert_land_evidence_record(str(project_id), {
                        **item,
                        "evidenceType": evidence_type,
                        "recordKey": f"{evidence_type}:{item['pnu']}",
                        "sourceLocator": {
                            "sourceType": "api_row",
                            "sourceUrl": item.get("sourceUrlOrEvidenceId"),
                            "apiRowId": item.get("pnu"),
                        },
                    })
            from storage import upsert_land_evidence_record
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
    supplied_law_context = options.get("lawContext") or options.get("law_context")
    mcp_attempt = None
    if not supplied_law_context:
        mcp_attempt = run_korean_law_mcp(project_data)
        supplied_law_context = mcp_attempt
    law_context = normalize_law_context(supplied_law_context)
    # Uploaded feasibility workbooks commonly use a list for schedule rows,
    # while the preliminary reviewer expects a keyed schedule object. Keep the
    # source untouched and provide a safe review projection.
    review_data = dict(project_data)
    for field in ("regulation", "covenants", "landEvidence"):
        if not isinstance(review_data.get(field), dict):
            review_data[field] = {}
    persisted_land_records = list_land_evidence_records(str(project_id))
    review_parcels = _parcel_source_rows(review_data)
    resolutions = list(land_enrichment.get("pnuResolutions") or [])
    resolution_index = 0
    if isinstance(review_parcels, list):
        for parcel in review_parcels:
            if not isinstance(parcel, dict) or parcel.get("pnu") or parcel.get("resolvedPnu"):
                continue
            if resolution_index >= len(resolutions):
                break
            resolved = resolutions[resolution_index].get("pnu")
            if resolved:
                parcel["pnu"] = resolved
                parcel["resolvedPnu"] = resolved
                parcel["pnuMatchMethod"] = resolutions[resolution_index].get("matchMethod")
                parcel["pnuResolveConfidence"] = resolutions[resolution_index].get("matchConfidence")
            resolution_index += 1
    cadastral = [item for item in persisted_land_records if item.get("evidenceType") in {"cadastral_characteristics", "cadastral"}]
    current_land_use = list(land_enrichment.get("landUsePlanningRecords") or [])
    persisted_land_use = [item for item in persisted_land_records if item.get("evidenceType") == "land_use_planning"]
    land_use_records = [*current_land_use, *persisted_land_use]
    if not review_data["regulation"].get("zoning"):
        zoning_record = next((item for item in land_use_records if item.get("zoning")), None) or next((item for item in cadastral if item.get("zoning")), None)
        if zoning_record:
            review_data["regulation"]["zoning"] = zoning_record.get("zoning")
            review_data["regulation"]["landUseZones"] = zoning_record.get("landUseZones") or []
            review_data["regulation"].setdefault("sourceLocator", {
                "sourceType": "api_row",
                "sourceUrl": zoning_record.get("sourceUrlOrEvidenceId"),
                "apiRowId": zoning_record.get("pnu"),
            })
    land_use_record = next((item for item in land_use_records if item.get("legalFar") is not None or item.get("legalBcr") is not None or item.get("districtUnitPlan")), None)
    if land_use_record:
        if land_use_record.get("legalFar") is not None:
            review_data["regulation"]["legal_far"] = land_use_record["legalFar"]
            review_data["regulation"]["legalFar"] = land_use_record["legalFar"]
        if land_use_record.get("legalBcr") is not None:
            review_data["regulation"]["legal_bcr"] = land_use_record["legalBcr"]
            review_data["regulation"]["legalBcr"] = land_use_record["legalBcr"]
        if land_use_record.get("districtUnitPlan") is not None:
            review_data["regulation"]["districtUnitPlan"] = land_use_record["districtUnitPlan"]
        review_data["regulation"].setdefault("sourceLocator", {
            "sourceType": "api_row",
            "sourceUrl": land_use_record.get("sourceUrlOrEvidenceId"),
            "apiRowId": land_use_record.get("pnu"),
        })
    if not isinstance(review_data.get("land"), dict):
        review_data["land"] = {}
    land_ledger = review_data.get("landLedger") if isinstance(review_data.get("landLedger"), dict) else {}
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
    if not review_data["land"].get("area_sqm") and cadastral:
        areas = [_number(item.get("areaSqm")) for item in cadastral]
        total_area = sum(item for item in areas if item is not None)
        if total_area:
            review_data["land"]["area_sqm"] = total_area
    if not isinstance(review_data.get("schedule"), dict):
        schedule_rows = review_data.get("schedule") if isinstance(review_data.get("schedule"), list) else []
        review_data["schedule"] = {"rows": schedule_rows}
    review = LegalReviewAgent().review(review_data, supplied_law_context)
    lifecycle_assessment = _legal_lifecycle_assessment(review_data, review, law_context)
    previous_law_records = [
        record for record in list_deal_state_records(str(project_id))
        if record.get("recordType") == "legal_evidence"
    ]
    law_change_report = compare_law_references(previous_law_records, review.get("lawReferences") or [])
    review_id = str(options.get("reviewId") or f"legal-review:{project_id}:{uuid4()}")
    parcels = _parcel_rows(str(project_id), review_data)
    parcel_blockers = [
        row for row in parcels
        if row["status"] == "needs_review"
        and (row.get("encumbrancePresent") or row.get("ownerMatchStatus") not in {None, "not_verified", "matched", "not_applicable"})
    ]
    parcel_pending = [row for row in parcels if row["status"] == "needs_review" and row not in parcel_blockers]
    locators = [locator for row in parcels for locator in row.get("sourceLocators", [])]
    if not locators:
        locators = [{"sourceType": "legal_review", "reviewId": review_id}]

    checks = []
    for check in review.get("checks", []):
        enriched = {
            **check,
            "checkCode": f"LEGAL_{str(check.get('key') or 'REVIEW').upper()}",
            "responsibleAgent": "LegalReviewAgent",
            "sourceLocators": locators[:10],
            "evidenceIds": [item.get("evidenceId") for item in locators if item.get("evidenceId")],
        }
        checks.append(enriched)

    status = "blocking_issue" if review.get("status") == "blocking_issue" or parcel_blockers else "caution" if review.get("status") == "caution" or parcel_pending else "clear"
    risk_issues = [_check_risk(check, locators, review_id) for check in checks if check.get("severity") in {"blocking", "caution"}]
    if parcel_pending:
        risk_issues.append({
            "recordType": "risk_issue", "recordId": "risk_issue:LEGAL_PARCEL_REGISTRY_EVIDENCE",
            "key": "LEGAL_PARCEL_REGISTRY_EVIDENCE", "code": "LEGAL_PARCEL_REGISTRY_EVIDENCE",
            "category": "legal", "severity": "caution", "status": "open", "reviewStatus": "open", "blocking": False,
            "description": f"{len(parcel_blockers)}개 필지의 등기·제한물권 증빙 또는 소유자 일치 확인이 필요합니다.",
            "mitigation": "필지별 등기부 증빙과 제한물권 말소 조건을 첨부하고 법무 검토를 완료합니다.",
            "sourceType": "land_evidence", "sourceLocator": locators[0], "evidenceIds": [item.get("evidenceId") for item in locators if item.get("evidenceId")],
            "legalReviewId": review_id,
        })
        risk_issues[-1]["description"] = f"{len(parcel_pending)}필지의 등기·제한물권 증빙이 아직 첨부되지 않았습니다."
        risk_issues[-1]["mitigation"] = "등기부 발급본과 제한물권 확인자료를 첨부한 뒤 법무 Pod를 재실행합니다."

    developability = review.get("developabilityAnalysis") or {}
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
        "sourceLocatorStatus": "complete" if locators and all(locators) else "weak",
        "mcp": {
            **review.get("mcp", {}),
            "connected": law_context.get("connected", False),
            "status": "connected" if law_context.get("connected") else "not_connected",
            "failureReason": None if law_context.get("connected") else "Korean Law MCP law_context was not supplied or returned no references.",
            "citationVerification": law_context.get("citationVerification"),
            "transport": (mcp_attempt or {}).get("transport") or law_context.get("source"),
            "diagnostics": (mcp_attempt or {}).get("diagnostics", []),
        },
        "riskIssues": risk_issues,
        "landEnrichment": land_enrichment,
        "legalLifecycleAssessment": lifecycle_assessment,
        "lawChangeReport": law_change_report,
        "landPodHandoff": land_pod_handoff,
        "updatedAt": _now(),
    }
    permit_record = {
        "reviewId": f"permit-review:{project_id}:{uuid4()}",
        "status": next((item.get("severity") for item in checks if item.get("key") == "permit"), "caution"),
        "reviewLevel": "preliminary",
        "permitCheck": next((item for item in checks if item.get("key") == "permit"), {}),
        "sourceLocators": locators,
        "parserStatus": "connected" if options.get("parsedDocuments") else "not_available",
        "parserFailureReason": None if options.get("parsedDocuments") else "인허가·법령·계약서 문서가 제공되지 않았습니다.",
        "updatedAt": _now(),
    }
    upsert_legal_review_record(str(project_id), legal_record)
    upsert_permit_review_record(str(project_id), permit_record)
    for index, reference in enumerate(review.get("lawReferences") or []):
        reference_id = str(reference.get("lawId") or reference.get("lawName") or reference.get("title") or f"reference-{index}")
        law_record = {
            "recordType": "legal_evidence",
            "recordId": f"legal_evidence:{project_id}:{review_id}:{index}",
            "key": reference_id,
            "valueType": "source",
            "lawReference": reference,
            "sourceType": "korean_law_mcp",
            "sourceLocator": {
                "sourceType": "api_row",
                "sourceUrl": reference.get("url") or "https://www.law.go.kr/",
                "apiRowId": reference.get("lawId") or reference_id,
                "article": reference.get("article"),
                "effectiveDate": reference.get("effectiveDate"),
            },
            "evidenceIds": [reference.get("lawId") or reference_id],
            "legalReviewId": review_id,
            "createdAt": _now(),
        }
        upsert_deal_state_record(str(project_id), law_record)
    for change_type in ("added", "removed", "changed"):
        for index, change in enumerate(law_change_report.get(change_type) or []):
            upsert_deal_state_record(str(project_id), {
                "recordType": "legal_law_change",
                "recordId": f"legal_law_change:{project_id}:{review_id}:{change_type}:{index}",
                "key": change.get("lawKey"),
                "changeType": change_type,
                "lawChange": change,
                "sourceType": "korean_law_mcp",
                "sourceLocator": {"sourceType": "api_row", "apiRowId": change.get("lawKey"), "sourceUrl": "https://www.law.go.kr/"},
                "legalReviewId": review_id,
                "status": "needs_review",
                "createdAt": _now(),
            })
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
        "sourceLocators": locators,
        "requiresHumanReview": status != "clear",
        "reviewStatus": "pending" if status != "clear" else "accepted",
        "createdAt": _now(),
    }
    upsert_deal_state_record(str(project_id), {
        "recordType": "handoff", "recordId": handoff["handoffId"], "key": handoff["handoffId"],
        "sourceType": "legal_pod", "sourceLocator": locators[0], **handoff,
    })
    legal_record["pfHandoff"] = handoff
    legal_record["nextActions"] = review.get("requiredFollowUps", [])
    return {"legalReview": legal_record, "permitReview": permit_record, "riskIssues": risk_issues, "handoff": handoff}
