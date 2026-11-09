from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study
from agents.cash_flow_engine import run_cash_flow_engine
from use_cases.approved_workbook_authority import (
    has_authoritative_workbook_snapshot_binding,
)
from use_cases.development_feasibility import analyze_development_feasibility_use_case
from use_cases.feasibility_workbook_llm import FeasibilityReviewConflict, feasibility_review_state
from core.reviewed_monthly_financing import reject_unsupported_monthly_financing_options, validate_monthly_financing_policy
from use_cases.cash_flow_timing import apply_timing_to_monthly_request


CASH_FLOW_REQUEST_TYPE = "cash_flow_run_request_v1"


def run_cash_flow_use_case(
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run Cash Flow Engine from a direct request or the canonical feasibility path."""
    input_data = input_data or {}
    request = _cash_flow_request(project_data, input_data)
    return execute_cash_flow_request_use_case(request)


def execute_cash_flow_request_use_case(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the server-prepared request that is persisted as the input snapshot."""
    return run_cash_flow_engine(request_data)


def build_cash_flow_request_use_case(
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Expose the authoritative request mapping for persistence and API use."""
    return _cash_flow_request(project_data, input_data or {})


def _cash_flow_request(project_data: Dict[str, Any], input_data: Dict[str, Any]) -> Dict[str, Any]:
    if "workingInputReview" in input_data:
        if set(input_data) != {"workingInputReview"}:
            raise ValueError("저장 검토 입력의 예산 계산과 다른 입력 변경을 한 요청에 섞을 수 없습니다.")
        return _working_input_request(project_data, input_data["workingInputReview"])
    if "reviewedMonthlyFinancing" in input_data:
        if set(input_data) != {"reviewedMonthlyFinancing"}:
            raise ValueError("월별 검토 계산과 다른 입력 변경을 한 요청에 섞을 수 없습니다.")
        return _reviewed_monthly_request(project_data, input_data["reviewedMonthlyFinancing"])
    direct_request = _direct_cash_flow_request(input_data)
    approved_request = _approved_canonical_cash_flow_request(project_data)
    if direct_request is not None:
        # A replay equal to the server's approved mapping cannot introduce
        # caller authority. Every differing direct request remains a candidate.
        if approved_request is not None and direct_request == approved_request:
            return approved_request
        if "capturedWorkbookCashFlow" in direct_request:
            registered_request = _registered_source_cash_flow_request(project_data)
            if registered_request is not None and direct_request == registered_request:
                return registered_request
            raise ValueError("원본 현금흐름은 서버에 등록된 동일 원본 계획으로만 계산할 수 있습니다. 원본 자료를 다시 선택하세요.")
        return _external_candidate_request(project_data, direct_request)

    if approved_request is not None:
        if input_data:
            raise ValueError(
                "partial cash-flow overrides are not allowed for an approved canonical workbook; "
                "submit a complete cash_flow_run_request_v1 or create a reviewed scenario"
            )
        return approved_request

    if not input_data:
        registered_request = _registered_source_cash_flow_request(project_data)
        if registered_request is not None:
            return registered_request

    analysis = analyze_development_feasibility_use_case(project_data, input_data)
    missing_amount_fields = analysis["pfReadiness"].get("missingFields", [])
    if missing_amount_fields:
        raise ValueError(
            "대출 금액이 미입력되어 현금흐름을 계산할 수 없습니다: "
            + ", ".join(missing_amount_fields)
        )
    request = _external_candidate_request(project_data, analysis["cashFlowRunRequestDraft"], allow_captured=True)
    # Only attach reviews to the saved working inputs they describe. Caller
    # overrides and approved canonical studies have their own source authority.
    if not input_data:
        _capture_working_input_review(request, project_data)
    return request


def _registered_source_cash_flow_request(project_data: Dict[str, Any]) -> Dict[str, Any] | None:
    """Use only the immutable server-extracted draft registered for this project.

    A caller's hashes and source labels cannot attest an external snapshot.
    Saved working inputs retain their normal calculation path instead of being
    silently replaced with an older workbook plan.
    """
    working_keys = ("feasibility_input", "pf_structure", "cost_assumptions", "sales_assumptions",
                    "product_mix", "productMix", "schedule", "project_schedule", "cashflow_input",
                    "sales_schedule", "salesCollectionSchedule")
    if any(project_data.get(key) for key in working_keys):
        return None
    draft = project_data.get("feasibilityStudyV2Draft")
    if not isinstance(draft, dict):
        return None
    raw = draft.get("raw_import") or {}
    if raw.get("profile") not in {"sangbong-sale-feasibility", "sangbong-private-rental-feasibility"}:
        return None
    from use_cases.workbook_import_approval import feasibility_study_draft_hash
    source, candidate = raw.get("sourceDocument") or {}, raw.get("candidateImport") or {}
    digest = source.get("originalFileHash")
    draft_hash = feasibility_study_draft_hash(draft)
    metadata = project_data.get("_meta") or {}
    history = metadata.get("feasibilityWorkbookCandidates") or []
    documents = project_data.get("source_documents") or []
    project_id = project_data.get("id") or (project_data.get("project") or {}).get("id")
    if not (project_id is not None and str(draft.get("project_id")) == str(project_id)
            and candidate.get("status") == "candidate" and candidate.get("draftHash") == draft_hash
            and candidate.get("importId") == metadata.get("latestFeasibilityWorkbookCandidateId")
            and any(isinstance(row, dict) and row.get("importId") == candidate.get("importId")
                    and row.get("draftHash") == draft_hash and digest in (row.get("sourceDocumentHashes") or [])
                    and row.get("status") == "candidate" for row in history)
            and any(isinstance(row, dict) and row.get("originalFileHash") == digest
                    and row.get("feasibilityStudyV2DraftHash") == draft_hash
                    and row.get("documentType") == "feasibility_workbook" for row in documents)):
        raise ValueError("저장된 원본 후보의 등록 기록·보관 파일·입력 해시를 확인할 수 없습니다. 원본 자료를 다시 불러오세요.")
    request = build_cash_flow_run_request_from_feasibility_study(draft)
    if "capturedWorkbookCashFlow" not in request:
        raise ValueError("원본 현금흐름의 기간·출처·입력 검증이 불완전합니다. 원본 자료를 다시 확인하세요.")
    return _external_candidate_request(project_data, request, allow_captured=True)


def _capture_working_input_review(request: Dict[str, Any], project_data: Dict[str, Any]) -> None:
    state = feasibility_review_state(project_data)
    request.setdefault("sourceMetadata", {})["inputReview"] = {
        "scope": "working_input_review_not_approval",
        "fieldReviews": deepcopy(state["fieldReviews"]),
        "termReviews": deepcopy(state["termReviews"]),
    }


def _working_input_request(project_data: Dict[str, Any], review: Any) -> Dict[str, Any]:
    """Explicit budget scenario from saved working inputs, never an original replay."""
    if not isinstance(review, dict) or set(review) != {"expectedReviewHash"}:
        raise ValueError("저장 검토 입력의 최신 상태를 확인한 뒤 예산 계산을 실행하세요.")
    expected = review["expectedReviewHash"]
    state = feasibility_review_state(project_data)
    if not isinstance(expected, str) or not expected or expected != state["reviewHash"]:
        raise FeasibilityReviewConflict("저장된 입력이 변경되었습니다. 최신 값을 다시 불러온 뒤 계산하세요.")
    analysis = analyze_development_feasibility_use_case(project_data)
    draft = analysis.get("cashFlowRunRequestDraft")
    if not isinstance(draft, dict):
        raise ValueError("예산 계산에 필요한 매출·사업비·자금조달 금액을 확인하세요.")
    missing = (analysis.get("pfReadiness") or {}).get("missingFields") or []
    if missing:
        raise ValueError("자금조달 금액을 확인한 뒤 예산 계산을 실행하세요: " + ", ".join(missing))
    request = _external_candidate_request(project_data, draft)
    if "monthlyFinancing" in request or "capturedWorkbookCashFlow" in request:
        raise ValueError("저장 검토 입력의 예산 계산에 원본 현금흐름이나 월별 약정 계산을 섞을 수 없습니다.")
    request["workingInputReview"] = {"reviewHash": expected, "basis": "saved_working_input_budget"}
    request["sourceMetadata"].update({"analysisOnly": True, "reportEligible": False,
                                      "sourceType": "saved_working_input_budget"})
    # The review form stores the aggregate PF amount separately from tranche
    # terms. Use that explicit review only as a budget limit when no detailed
    # financing exists; it cannot create a loan agreement or monthly policy.
    aggregate_review = state["fieldReviews"].get("pf_amount")
    aggregate_amount = state["values"].get("pf_amount")
    if not project_data.get("pf_structure") and isinstance(aggregate_review, dict):
        if (isinstance(aggregate_amount, bool) or not isinstance(aggregate_amount, (int, float))
                or aggregate_amount < 0 or aggregate_amount != int(aggregate_amount)):
            raise ValueError("검토한 PF 총액의 원 단위 금액을 확인하세요.")
        request["sourcesAndUses"].setdefault("funding", {})["pfLoan"] = int(aggregate_amount)
        request["sourceMetadata"]["aggregateFundingReview"] = {
            "scope": "analysis_limit_not_loan_agreement", "valueWon": int(aggregate_amount),
            "reviewHash": expected, "review": deepcopy(aggregate_review),
        }
    _capture_working_input_review(request, project_data)
    return request


def _reviewed_monthly_request(project_data: Dict[str, Any], review: Any) -> Dict[str, Any]:
    """Build an explicit working-input scenario without changing an approved study."""
    if not isinstance(review, dict) or set(review) != {"policy", "reason", "expectedReviewHash"}:
        raise ValueError("월별 금융 검토의 계산 방식·이유·최신 입력 상태를 확인하세요.")
    policy = validate_monthly_financing_policy(review["policy"])
    if policy is None:
        raise ValueError("월별 금융 검토 방식을 직접 선택하세요.")
    reason = review["reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
        raise ValueError("월별 금융 검토 이유를 입력하세요(4000자 이하).")
    expected = review["expectedReviewHash"]
    if not isinstance(expected, str) or not expected:
        raise ValueError("최신 검토 입력을 불러온 뒤 계산하세요.")
    if expected != feasibility_review_state(project_data)["reviewHash"]:
        raise FeasibilityReviewConflict("저장된 입력이 변경되었습니다. 최신 값을 다시 불러온 뒤 계산하세요.")
    raw_financing = project_data.get("pf_structure")
    reject_unsupported_monthly_financing_options(raw_financing)
    if not isinstance(raw_financing, list) or not raw_financing or any(not isinstance(row, dict) for row in raw_financing):
        raise ValueError("월별 금융 검토에는 대출 조건 행 목록이 필요합니다. 원본 금융 조건을 확인해 주세요.")
    analysis = analyze_development_feasibility_use_case(project_data)
    draft = analysis.get("cashFlowRunRequestDraft")
    if not isinstance(draft, dict):
        raise ValueError("금액이 미입력된 자금조달 항목을 확인한 뒤 계산하세요.")
    request = _external_candidate_request(project_data, draft)
    request["monthlyFinancing"] = policy
    request["reviewedMonthlyFinancingReview"] = {"reason": reason.strip(), "reviewHash": expected}
    request["sourceMetadata"].update({"analysisOnly": True, "reportEligible": False,
                                      "sourceType": "reviewed_working_input_scenario"})
    request = apply_timing_to_monthly_request(project_data, request)
    _capture_working_input_review(request, project_data)
    return request


def _direct_cash_flow_request(input_data: Dict[str, Any]) -> Dict[str, Any] | None:
    if input_data.get("requestType") == CASH_FLOW_REQUEST_TYPE:
        return input_data
    for key in ("cashFlowRunRequest", "cashFlowRunRequestDraft"):
        candidate = input_data.get(key)
        if isinstance(candidate, dict) and candidate.get("requestType") == CASH_FLOW_REQUEST_TYPE:
            return candidate
    return None


def _approved_canonical_cash_flow_request(project_data: Dict[str, Any]) -> Dict[str, Any] | None:
    canonical = project_data.get("canonicalFeasibilityStudyV2")
    if isinstance(canonical, dict) and has_authoritative_workbook_snapshot_binding(
        canonical
    ):
        return build_cash_flow_run_request_from_feasibility_study(canonical)
    return None


def _external_candidate_request(
    project_data: Dict[str, Any],
    direct_request: Dict[str, Any],
    *, allow_captured: bool = False,
) -> Dict[str, Any]:
    """Copy and de-authorize an API-supplied CashFlowRunRequest.

    Approval is a server-side fact. A caller may submit a complete scenario for
    calculation, but fields inside that scenario cannot assert that its cost,
    sales, or collection-allocation schedules are approved or report-grade.
    """
    if "capturedWorkbookCashFlow" in direct_request and not allow_captured:
        raise ValueError("외부 요청에서 원본 현금흐름 출처를 직접 선언할 수 없습니다.")
    candidate = deepcopy(direct_request)
    _downgrade_untrusted_approval_claims(candidate)
    _bind_route_project_identity(candidate, project_data)

    candidate["authorityStatus"] = "candidate"
    candidate["reportEligible"] = False

    source_metadata = candidate.get("sourceMetadata")
    if not isinstance(source_metadata, dict):
        source_metadata_alias = candidate.get("source_metadata")
        source_metadata = dict(source_metadata_alias) if isinstance(source_metadata_alias, dict) else {}
        candidate["sourceMetadata"] = source_metadata
    _mark_candidate_section(source_metadata)
    # A complete caller-supplied scenario cannot forge the server's review
    # history. Working-input paths attach verified stored records afterwards.
    source_metadata.pop("inputReview", None)

    for key in (
        "source_metadata",
        "costSchedule",
        "cost_schedule",
        "collectionAllocationRules",
        "collection_allocation_rules",
    ):
        section = candidate.get(key)
        if isinstance(section, dict):
            _mark_candidate_section(section)
    return candidate


def _downgrade_untrusted_approval_claims(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _downgrade_untrusted_approval_claims(item)
        return
    if not isinstance(value, dict):
        return

    for key, item in list(value.items()):
        normalized_key = str(key).replace("_", "").casefold()
        if normalized_key == "approvalstatus":
            value[key] = "unapproved"
        elif normalized_key == "canonicalstatus":
            value[key] = "candidate"
        elif normalized_key == "approval":
            value[key] = {}
        elif normalized_key == "reporteligible":
            value[key] = False
        elif normalized_key in {"approved", "isapproved", "authoritative", "isauthoritative"} and isinstance(item, bool):
            value[key] = False
        else:
            _downgrade_untrusted_approval_claims(item)


def _mark_candidate_section(section: Dict[str, Any]) -> None:
    section["approvalStatus"] = "unapproved"
    section["approval"] = {}
    section["authorityStatus"] = "candidate"
    section["reportEligible"] = False
    source_type = str(section.get("sourceType") or "")
    if source_type.casefold().startswith("approved"):
        section["sourceType"] = "external_request_candidate"


def _bind_route_project_identity(request: Dict[str, Any], project_data: Dict[str, Any]) -> None:
    route_project = project_data.get("project")
    route_project = route_project if isinstance(route_project, dict) else {}
    project_id = (
        project_data.get("id")
        or route_project.get("id")
        or project_data.get("projectId")
        or project_data.get("project_id")
    )
    project_name = (
        route_project.get("name")
        or project_data.get("projectName")
        or project_data.get("project_name")
        or project_data.get("name")
    )

    request_project = request.get("project")
    request_project = dict(request_project) if isinstance(request_project, dict) else {}
    request_project.pop("id", None)
    request_project.pop("project_id", None)
    if project_id not in (None, ""):
        authoritative_id = str(project_id)
        request_project["projectId"] = authoritative_id
        for key in ("projectId", "project_id"):
            if key in request:
                request[key] = authoritative_id
    else:
        request_project.pop("projectId", None)
        request.pop("projectId", None)
        request.pop("project_id", None)
    if project_name not in (None, ""):
        request_project["projectName"] = str(project_name)
    else:
        request_project.pop("projectName", None)
    request["project"] = request_project
