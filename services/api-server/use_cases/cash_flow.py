from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study
from agents.cash_flow_engine import run_cash_flow_engine
from use_cases.approved_workbook_authority import (
    has_authoritative_workbook_snapshot_binding,
)
from use_cases.development_feasibility import analyze_development_feasibility_use_case


CASH_FLOW_REQUEST_TYPE = "cash_flow_run_request_v1"


def run_cash_flow_use_case(
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run Cash Flow Engine from a direct request or the canonical feasibility path."""
    input_data = input_data or {}
    request = _cash_flow_request(project_data, input_data)
    return run_cash_flow_engine(request)


def build_cash_flow_request_use_case(
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Expose the authoritative request mapping for persistence and API use."""
    return _cash_flow_request(project_data, input_data or {})


def _cash_flow_request(project_data: Dict[str, Any], input_data: Dict[str, Any]) -> Dict[str, Any]:
    direct_request = _direct_cash_flow_request(input_data)
    approved_request = _approved_canonical_cash_flow_request(project_data)
    if direct_request is not None:
        # The API route first builds the canonical request and then passes that
        # exact in-process value back into ``run_cash_flow_use_case``. Preserve
        # that server-derived path, while treating every differing request as an
        # external scenario candidate. An external replay that is byte-for-byte
        # equivalent to the canonical mapping cannot introduce caller authority.
        if approved_request is not None and direct_request == approved_request:
            return approved_request
        return _external_candidate_request(project_data, direct_request)

    if approved_request is not None:
        if input_data:
            raise ValueError(
                "partial cash-flow overrides are not allowed for an approved canonical workbook; "
                "submit a complete cash_flow_run_request_v1 or create a reviewed scenario"
            )
        return approved_request

    analysis = analyze_development_feasibility_use_case(project_data, input_data)
    missing_amount_fields = analysis["pfReadiness"].get("missingFields", [])
    if missing_amount_fields:
        raise ValueError(
            "대출 금액이 미입력되어 현금흐름을 계산할 수 없습니다: "
            + ", ".join(missing_amount_fields)
        )
    return analysis["cashFlowRunRequestDraft"]


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
) -> Dict[str, Any]:
    """Copy and de-authorize an API-supplied CashFlowRunRequest.

    Approval is a server-side fact. A caller may submit a complete scenario for
    calculation, but fields inside that scenario cannot assert that its cost,
    sales, or collection-allocation schedules are approved or report-grade.
    """
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
