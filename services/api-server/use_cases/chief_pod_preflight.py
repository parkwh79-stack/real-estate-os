"""Read-only Chief Pod preparation using the same track graph as formal runs."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from typing import Any

from integrations.market_data import MolitRtmsConnector, lawd_cd_from_pnu
from integrations.vworld import fetch_parcel_by_pnu, vworld_configured
from schemas.orchestration import AgentHandoffEnvelope, MissingInputRegisterItem
from use_cases.cash_flow import build_cash_flow_request_use_case
from use_cases.chief_deal_orchestration import _agent_pods, _missing_inputs, _workflow_track
from use_cases.chief_pod_records import stable_digest
from use_cases.chief_pod_work_orders import POD_EXECUTION_AGENTS
from use_cases.market_comparables import ASSET_TYPE_MAP, fetch_project_market_comparables, recent_deal_yms
from use_cases.parcel_pnu_resolution import canonical_project_parcel_rows, parcel_pnu, resolve_parcel_rows
from use_cases.pf_structure import build_pf_structure_input
from use_cases.pod_execution_coordinator import PodExecutionCoordinator


SCHEMA_VERSION = "realytics-chief-pod-preflight-v1"
MAX_LIVE_PARCELS = 2

POD_PURPOSES = {
    "land_legal": "사업 대상 필지와 토지 권리·인허가 조건 확인",
    "market_product": "분양·임대 가격과 상품계획 검토",
    "cost_schedule": "공사비와 사업 일정 검토",
    "feasibility_cashflow": "사업수지와 월별 현금흐름 입력 확인",
    "pf_credit": "대출·담보·상환 조건 검토",
    "public_policy": "도시계획과 공공기여 조건 검토",
}

CHECK_LABELS = {
    "parcels": "사업 대상 필지",
    "zoning": "용도지역·도시계획 자료",
    "registry": "등기·제한물권 근거",
    "market": "비교 가능한 시장 자료",
    "product": "상품 구성",
    "cost": "사업비·공사비",
    "schedule": "사업 일정",
    "revenue": "사업수입",
    "cash_flow": "현금흐름 입력",
    "financing": "PF 대출조건",
    "policy": "공공기여·공모 조건",
}

# Candidate matches are only used to send users to review an existing document.
# A candidate never satisfies a missing financial or legal input.
CANDIDATE_KEYS = {
    "pnu_or_land_ledger_missing": {"land.pnu"},
    "regulation_data_missing": {"land.zoning", "regulation.zoning"},
    "revenue_assumption_missing": {"feasibility.totalrevenue", "feasibility_input.total_revenue"},
    "cost_assumption_missing": {"feasibility.totalcost", "feasibility_input.total_cost", "feasibility_input.construction_cost"},
    "pf_structure_missing": {"pf.loanamount", "feasibility.pfamount", "feasibility_input.pf_amount"},
}

LOCATOR_FIELDS = (
    "sourceType", "sourceName", "sourceDocumentId", "documentId", "documentName",
    "archiveId", "sheetName", "rowNumber", "cell", "cellRange", "pageNumber",
    "apiRowId", "evidenceId", "reference",
)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _locators(*values: Any) -> list[dict[str, Any]]:
    locators: list[dict[str, Any]] = []
    for value in values:
        row = _object(value)
        locator = {
            key: row[key] for key in LOCATOR_FIELDS
            if isinstance(row.get(key), (str, int)) and row[key] != ""
        }
        if locator and locator not in locators:
            locators.append(locator)
    return locators[:12]


def _working_input(label: str) -> dict[str, str]:
    return {"sourceType": "project_input", "sourceName": "저장된 프로젝트 입력", "reference": label}


def _check(
    key: str, value: Any, *sources: dict[str, Any],
    status: str | None = None, basis: str = "working_input",
) -> dict[str, Any]:
    state = status or ("available" if value else "missing")
    return {
        "key": key,
        "label": CHECK_LABELS[key],
        "status": state,
        "basis": basis if state != "missing" else None,
        "sourceLocators": _locators(*(sources or (_working_input(CHECK_LABELS[key]),))) if state != "missing" else [],
    }


def _number_present(values: tuple[Any, ...], *, positive: bool = True) -> bool:
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        try:
            number = float(str(value).replace(",", ""))
        except ValueError:
            continue
        if isfinite(number) and (number > 0 if positive else number >= 0):
            return True
    return False


def _public_status(key: str, label: str, status: str, message: str) -> dict[str, str]:
    return {"key": key, "label": label, "status": status, "message": message}


def _confirmed_parcels(
    project_id: str, data: dict[str, Any], *, allow_live: bool,
) -> tuple[set[str], dict[str, str]]:
    """Verify at most two registered parcel keys, never a nearby inferred site."""
    parcels = canonical_project_parcel_rows(data)
    site = _object(data.get("land"))
    candidates = parcels or ([site] if parcel_pnu(site) else [])
    evidence = _rows(_object(data.get("landEvidence") or data.get("land_evidence")).get("records"))
    stored = {
        str(row["pnu"]) for row in evidence
        if row.get("pnu") and (
            row.get("sourcePnuVerified") is True or (
                row.get("evidenceType") == "pnu_resolution"
                and row.get("verificationStatus") == "resolved"
                and row.get("matchMethod") in {"pnu_construction", "address_geocode"}
            )
        )
    }

    def existing_verified(row: dict[str, Any]) -> bool:
        return (
            parcel_pnu(row) in stored or (
                row.get("pnuMatchMethod") in {"pnu_construction", "address_geocode"}
                and _object(row.get("pnuResolution")).get("lotVerification") == "verified"
            )
        )

    checked = {parcel_pnu(row) for row in candidates if parcel_pnu(row) and existing_verified(row)}
    label = "VWorld 지적도"
    if not candidates:
        return checked, _public_status("parcels", label, "needs_input", "저장된 토지조서 또는 사업 대상 PNU가 필요합니다.")
    pending = [(index, row) for index, row in enumerate(candidates, start=1) if not parcel_pnu(row) or parcel_pnu(row) not in checked]
    if not pending:
        return checked, _public_status("parcels", label, "available", "기존에 대조한 지적 근거를 사용했습니다.")
    if not allow_live or not vworld_configured():
        return checked, _public_status("parcels", label, "unavailable", "공공 지적도 연결이 설정되지 않았습니다.")

    pending = pending[:MAX_LIVE_PARCELS]
    exact = {parcel_pnu(row) for _, row in pending if parcel_pnu(row)}
    unresolved = {
        index for index, row in pending
        if not parcel_pnu(row)
        and _object(row.get("pnuResolution")).get("reason") != "conflicting_existing_pnu"
    }
    new_matches = set()
    for pnu in sorted(exact):
        properties, diagnostics = fetch_parcel_by_pnu(pnu, timeout=2.0)
        if diagnostics.get("status") == "resolved" and _object(properties).get("pnu") == pnu:
            checked.add(pnu)
            new_matches.add(pnu)
    if unresolved:
        resolve_parcel_rows(data, project_id, limit=MAX_LIVE_PARCELS, parcel_indexes=unresolved, timeout_seconds=2.0)
        for index, row in enumerate(canonical_project_parcel_rows(data), start=1):
            if index in unresolved and parcel_pnu(row) and existing_verified(row):
                pnu = parcel_pnu(row)
                checked.add(pnu)
                new_matches.add(pnu)
    if not checked and not exact and not unresolved:
        return checked, _public_status("parcels", label, "needs_input", "서로 다른 PNU가 적힌 필지는 원본에서 대조해 주세요.")
    return checked, _public_status(
        "parcels", label, "checked" if new_matches else "failed",
        "검증된 PNU만 후속 검토에 사용했습니다. 전체 필지는 토지·인허가 미리보기에서 확인할 수 있습니다."
        if checked else "공공 지적도에서 대상 필지를 대조하지 못했습니다.",
    )


def _market_lookup(
    data: dict[str, Any], parcel_basis: dict[str, Any], *,
    allow_live: bool,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    label = "국토교통부 실거래가"
    market = _object(data.get("market") or data.get("marketEvidenceReview"))
    saved = _rows(market.get("transactionComparables")) + _rows(market.get("manualComparables"))
    if saved:
        return [], _public_status("market", label, "available", "저장된 시장 비교자료를 우선 사용했습니다.")
    districts = parcel_basis.get("districtCodes", [])
    if parcel_basis.get("status") != "available" or len(districts) != 1:
        return [], _public_status("market", label, "needs_input", "필지와 단일 시군구를 확인한 뒤 해당 지역의 실거래가를 조회합니다.")
    product = str(_object(data.get("project")).get("type") or _object(data.get("marketability")).get("productType") or "").strip().lower()
    asset = ASSET_TYPE_MAP.get(product)
    if not asset or product in {"mixed_use", "주상복합", "serviced_residence", "생활숙박시설", "생숙"}:
        return [], _public_status("market", label, "needs_input", "비교할 자산 유형이 명확한 경우에 해당 유형의 거래만 조회합니다.")
    connector = MolitRtmsConnector()
    if not allow_live or not connector.configured():
        return [], _public_status("market", label, "unavailable", "실거래가 연결이 설정되지 않았습니다.")
    lawd = districts[0]
    months = recent_deal_yms(1)
    records, diagnostics = fetch_project_market_comparables(
        data, connector=connector, deal_yms=months, asset_type=asset, lawd_cd=lawd,
        per_month_rows=30, max_records=12,
    )
    if not records:
        return [], _public_status(
            "market", label, "checked" if diagnostics.get("status") == "empty" else "failed",
            "조회 기간에 검토할 거래가 없거나 해당 자료를 확인하지 못했습니다.",
        )
    return [{"sourceType": "public_api", "sourceName": label, "reference": f"{lawd} · {months[0]} · {len(records)}건"}], _public_status(
        "market", label, "checked", "해당 시군구와 자산 유형의 거래 자료를 확인했습니다. 개별 가격 적용은 검토가 필요합니다.",
    )


def _candidate_locators(data: dict[str, Any], item_id: str) -> list[dict[str, Any]]:
    accepted_keys = CANDIDATE_KEYS.get(item_id, set())
    original_ids = {
        str(document.get("archiveId") or document.get("originalFileHash"))
        for document in _rows(data.get("source_documents") or data.get("sourceDocuments"))
        if document.get("archiveId") or document.get("originalFileHash")
    }
    locators = []
    for record in _rows(data.get("parsedFacts")):
        key = str(record.get("canonicalField") or record.get("factKey") or "").casefold()
        if key not in accepted_keys or record.get("validationStatus") in {"rejected", "superseded"}:
            continue
        location = _object(record.get("sourceLocator"))
        source = _object(record.get("sourceDocument"))
        document_id = str(source.get("archiveId") or location.get("documentId") or "")
        if document_id in original_ids:
            locators.extend(_locators({
                **location, "archiveId": document_id, "sourceType": "uploaded_document",
                "documentName": source.get("originalFileName") or location.get("documentName") or "보관 원본",
            }))
    return _locators(*locators)


def _evidence_locators(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _locators(*(
        {**_object(row.get("sourceLocator")), **({"evidenceId": row["evidenceId"]} if row.get("evidenceId") else {})}
        for row in rows
    ))


def _financing_check(data: dict[str, Any]) -> dict[str, Any]:
    tranches = build_pf_structure_input(data).get("tranches")
    if not tranches:
        return _check("financing", False)
    rows = _rows(tranches)
    complete = isinstance(tranches, list) and len(rows) == len(tranches) and all(
        _number_present((row.get("amount"),))
        and _number_present((row.get("interest_rate", row.get("rate")),), positive=False)
        for row in rows
    )
    return _check("financing", True, status="available" if complete else "needs_review")


def run_chief_pod_preflight(
    project_id: str, project_data: dict[str, Any], *, workflow_track: str | None = None, allow_public_lookups: bool = True,
) -> dict[str, Any]:
    """Run independent input checks once in the track DAG. Only in-memory evidence is exchanged."""
    data = deepcopy(project_data)
    track = _workflow_track(data, {"workflowTrack": workflow_track})
    missing = _missing_inputs(data)
    pods = _agent_pods(missing, track)
    order = PodExecutionCoordinator().topological_order(pods)
    by_key = {pod.pod_key: pod for pod in pods}
    snapshot = stable_digest({"projectId": project_id, "workflowTrack": track, "data": data})
    now = datetime.now(timezone.utc).isoformat()
    pod_results = []
    public_lookups = []
    handoffs: list[AgentHandoffEnvelope] = []

    financing_check = _financing_check(data)

    for pod_key in order:
        pod = by_key[pod_key]
        incoming = [handoff for handoff in handoffs if handoff.payload["toPod"] == pod_key]
        inherited = {
            check["key"]: check for handoff in incoming for check in handoff.payload["availableChecks"]
        }
        feasibility = _object(data.get("feasibility_input") or data.get("feasibilityStudy"))
        regulation = _object(data.get("regulation") or data.get("zoning"))
        cost = _check("cost", _number_present((feasibility.get("total_cost"), feasibility.get("construction_cost"))))
        schedule = _check("schedule", data.get("schedule") or data.get("project_schedule"))
        if pod_key == "land_legal":
            confirmed, lookup = _confirmed_parcels(project_id, data, allow_live=allow_public_lookups)
            public_lookups.append(lookup)
            land = _object(data.get("land"))
            parcels = canonical_project_parcel_rows(data) or ([land] if parcel_pnu(land) else [])
            locators = [
                {"sourceType": "api_row", "sourceName": "VWorld 지적도", "apiRowId": pnu} for pnu in sorted(confirmed)
            ]
            parcel_check = _check(
                "parcels", parcels, *locators, basis="verified_public" if confirmed else "working_input",
                status="available" if confirmed and len(confirmed) == len(parcels) else "needs_review" if parcels else "missing",
            )
            parcel_check["districtCodes"] = sorted({code for pnu in confirmed if (code := lawd_cd_from_pnu(pnu))})
            evidence = _rows(_object(data.get("landEvidence") or data.get("land_evidence")).get("records"))
            zoning = [
                row for row in evidence
                if row.get("evidenceType") == "land_use_planning" and row.get("pnu") in confirmed
                and row.get("sourcePnuVerified") is True and (row.get("zoning") or row.get("districtUnitPlan"))
            ]
            registry = [
                row for row in evidence if row.get("evidenceType") == "registry_record" and row.get("pnu") in confirmed
            ]
            zoning_locators = _evidence_locators(zoning)
            registry_locators = _evidence_locators(registry)
            all_parcels_verified = parcel_check["status"] == "available"
            zoning_complete = all_parcels_verified and {row["pnu"] for row in zoning} == confirmed
            registry_complete = all_parcels_verified and {row["pnu"] for row in registry} == confirmed
            checks = [
                parcel_check,
                _check("zoning", zoning_locators or regulation.get("zoning") or regulation.get("districtUnitPlan"),
                       *zoning_locators, basis="stored_evidence" if zoning_locators else "working_input",
                       status="needs_review" if zoning_locators and not zoning_complete else None),
                _check("registry", registry_locators, *registry_locators, basis="stored_evidence",
                       status="needs_review" if registry_locators and not registry_complete else None),
            ]
        elif pod_key == "market_product":
            parcel_basis = inherited.get("parcels", {})
            locators, lookup = _market_lookup(data, parcel_basis, allow_live=allow_public_lookups)
            public_lookups.append(lookup)
            market = _object(data.get("market") or data.get("marketEvidenceReview"))
            checks = [
                _check("market", locators or _rows(market.get("transactionComparables")) or _rows(market.get("manualComparables")),
                       *locators, basis="verified_public" if locators else "working_input"),
                _check("product", _rows(data.get("product_mix") or data.get("productMix"))),
            ]
        elif pod_key == "cost_schedule":
            checks = [cost, schedule]
        elif pod_key == "feasibility_cashflow":
            revenue = _check("revenue", _number_present((feasibility.get("total_revenue"), feasibility.get("sales_revenue"))))
            cost = inherited.get("cost", cost)
            schedule = inherited.get("schedule", schedule)
            cash_flow = _check("cash_flow", False)
            if all(row["status"] == "available" for row in (revenue, cost, schedule)):
                cash_flow = _check("cash_flow", True, status="needs_review")
                if financing_check["status"] != "needs_review":
                    try:
                        build_cash_flow_request_use_case(data)
                    except (ValueError, TypeError):
                        pass
                    else:
                        cash_flow = _check("cash_flow", True)
            checks = [revenue, cost, schedule, cash_flow]
        elif pod_key == "pf_credit":
            checks = [financing_check, inherited.get("cash_flow", _check("cash_flow", False))]
        else:
            checks = [
                inherited.get("zoning", _check("zoning", regulation.get("zoning") or regulation.get("districtUnitPlan"))),
                _check("policy", data.get("publicContribution") or data.get("public_contribution") or data.get("policy")),
            ]

        available = [
            check for check in checks
            if check["status"] == "available"
            or (check["key"] == "parcels" and check["basis"] == "verified_public")
        ]
        sent = []
        for downstream_key in PodExecutionCoordinator().downstream_pods(pod_key, pods):
            if not available:
                continue
            handoff_id = "chief-preflight:" + stable_digest([snapshot, pod_key, downstream_key, available])[:24]
            handoffs.append(AgentHandoffEnvelope(
                handoffId=handoff_id, workflow="chief-deal-orchestration", runId=None,
                handoffType="pod_preflight_evidence", payloadSchema=SCHEMA_VERSION,
                fromAgent=POD_EXECUTION_AGENTS[pod_key][0], toAgent=POD_EXECUTION_AGENTS[downstream_key][0],
                status="accepted", createdAt=now,
                payload={"fromPod": pod_key, "toPod": downstream_key, "analysisOnly": True, "availableChecks": available},
                evidenceIds=list(dict.fromkeys(str(locator["evidenceId"]) for check in available
                                              for locator in check["sourceLocators"] if locator.get("evidenceId"))),
            ))
            sent.append(handoff_id)
        pod_results.append({
            "podKey": pod_key, "status": "checked" if all(check["status"] == "available" for check in checks) else
            "partial" if available or any(check["status"] == "needs_review" for check in checks) else "needs_material",
            "checks": checks, "receivedHandoffIds": [handoff.handoff_id for handoff in incoming], "sentHandoffIds": sent,
        })

    by_pod = {result["podKey"]: {row["key"]: row for row in result["checks"]} for result in pod_results}
    cleared = {
        item_id for item_id, pod_key, check_key in (
            ("pnu_or_land_ledger_missing", "land_legal", "parcels"),
            ("market_evidence_missing", "market_product", "market"),
        ) if by_pod[pod_key][check_key]["status"] == "available"
    }
    # The legacy register is coarse for some inputs (any financing row,
    # any land record). Preserve remaining evidence requirements discovered
    # by these narrower checks, without using them as approval gates.
    missing_by_id = {item.item_id: item for item in missing}
    for item_id, pod_key, check_key, category, message, targets, sources in (
        ("pnu_or_land_ledger_missing", "land_legal", "parcels", "land",
         "아직 대조되지 않은 대상 필지의 지번과 범위를 확인해 주세요.", ["land_legal", "public_policy"], ["토지조서", "토지대장"]),
        ("registry_evidence_missing", "land_legal", "registry", "legal",
         "대상 필지의 등기·제한물권 근거가 필요합니다.", ["land_legal", "pf_credit"], ["등기사항증명서", "담보·권리 자료"]),
        ("cost_assumption_missing", "cost_schedule", "cost", "cost",
         "사업비 또는 공사비의 입력값을 확인해 주세요.", ["cost_schedule", "feasibility_cashflow"], ["사업수지 Excel", "공사비 내역"]),
        ("revenue_assumption_missing", "feasibility_cashflow", "revenue", "feasibility",
         "사업수입의 입력값을 확인해 주세요.", ["feasibility_cashflow"], ["사업수지 Excel", "분양가표"]),
        ("pf_structure_missing", "pf_credit", "financing", "pf",
         "대출조건에서 원금과 금리가 빠진 항목을 확인해 주세요.", ["pf_credit"], ["대출조건표", "PF Term Sheet"]),
    ):
        if by_pod[pod_key][check_key]["status"] != "available" and item_id not in missing_by_id:
            missing_by_id[item_id] = MissingInputRegisterItem(
                itemId=item_id, category=category, description=message, requiredFor=targets,
                severity="high", acceptableSources=sources,
            )

    zoning_check = by_pod["land_legal"]["zoning"]
    if zoning_check["status"] == "available" and zoning_check["basis"] == "stored_evidence":
        cleared.add("regulation_data_missing")
    if "land_evidence_missing" in missing_by_id:
        # The legacy request already includes registry evidence.
        cleared.add("registry_evidence_missing")

    requests = []
    remaining = (
        [missing_by_id["source_documents_missing"]] if "source_documents_missing" in missing_by_id
        else list(missing_by_id.values())
    )
    for item in remaining:
        if item.item_id in cleared:
            continue
        pod_keys = [key for key in order if key in item.required_for or "all_pods" in item.required_for]
        candidate_locators = _candidate_locators(data, item.item_id)
        requests.append({
            **item.model_dump(by_alias=True),
            "podKeys": pod_keys,
            "purpose": " · ".join(POD_PURPOSES[key] for key in pod_keys),
            "action": "review_document" if candidate_locators else "provide_material",
            "sourceLocators": candidate_locators,
        })
    return {
        "schemaVersion": SCHEMA_VERSION, "projectId": project_id, "workflowTrack": track,
        "inputRevision": snapshot, "analysisOnly": True, "pods": pod_results,
        "materialRequests": requests, "publicLookups": public_lookups,
        "handoffs": [handoff.model_dump(by_alias=True) for handoff in handoffs],
    }
