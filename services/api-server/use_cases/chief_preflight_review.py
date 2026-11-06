"""Bounded, read-only business Agent reviews before requesting more documents."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Callable

from agents.credit_review_agent import CreditReviewAgent
from agents.legal_review_agent import LegalReviewAgent
from agents.market_evidence_agent import MarketEvidenceAgent
from agents.market_product_depth_agent import MarketProductDepthAgent
from agents.pod_ai_reviewer_agent import PodAIReviewerAgent
from agents.risk_register_agent import RiskRegisterAgent
from integrations.market_data import MolitRtmsConnector, _http_get, lawd_cd_from_pnu
from schemas.orchestration import AgentHandoffEnvelope
from use_cases.cash_flow import build_cash_flow_request_use_case, execute_cash_flow_request_use_case
from use_cases.chief_deal_orchestration import _agent_pods, _missing_inputs, _workflow_track
from use_cases.chief_pod_preflight import POD_PURPOSES, _confirmed_parcels, run_chief_pod_preflight
from use_cases.chief_pod_records import stable_digest
from use_cases.chief_pod_work_orders import route_review_work_orders
from use_cases.market_comparables import ASSET_TYPE_MAP, fetch_project_market_comparables, recent_deal_yms
from use_cases.parcel_pnu_resolution import canonical_project_parcel_rows, parcel_pnu
from use_cases.common import deep_merge
from use_cases.covenant_eod import evaluate_covenant_eod_use_case
from use_cases.developability_score import score_developability_use_case
from use_cases.development_feasibility import analyze_development_feasibility_use_case, development_feasibility_input_basis
from use_cases.legal_pod import run_legal_pod_use_case
from use_cases.marketability_review import analyze_marketability_use_case
from use_cases.pf_structure import build_pf_structure_input, execute_pf_structure_input
from use_cases.pod_execution_coordinator import PodExecutionCoordinator

RUNNER_VERSION = "realytics-chief-preflight-review-v2"

# These are consumed source inputs, not previous generated domain projections.
_INPUT_KEYS = {
    "land_legal": ("project", "land", "landLedger", "land_ledger", "land_acquisition", "landEvidence", "land_evidence",
                   "regulation", "regulatoryConstraintSnapshot", "legalContext", "covenants", "source_documents",
                   "sourceDocuments", "parsedFacts", "permit", "permits"),
    "market_product": ("project", "market", "marketEvidence", "market_evidence", "documentMarketEvidence",
                       "document_market_evidence", "documentParsedMarketFacts", "product_mix", "productMix",
                       "land", "landLedger", "regulation", "architectural_plan", "source_documents", "parsedFacts"),
    "cost_schedule": ("project", "land", "regulation", "architectural_plan", "feasibility_input", "costs",
                      "cost_assumptions", "sales_assumptions", "schedule", "project_schedule", "source_documents",
                      "feasibilityStudyV2", "canonicalFeasibilityStudyV2", "logistics", "version"),
    "feasibility_cashflow": ("project", "land", "regulation", "architectural_plan", "feasibility_input",
                            "cost_assumptions", "sales_assumptions", "schedule", "project_schedule", "cashflow_input",
                            "sales_schedule", "salesCollectionSchedule", "product_mix", "productMix", "pf_structure",
                            "feasibilityStudyV2", "canonicalFeasibilityStudyV2", "logistics", "version", "_meta",
                            "activeAssumptionOverrides", "feasibilityStudyV2Draft", "expected_results"),
    "pf_credit": ("project", "land", "landEvidence", "regulation", "pf_structure", "feasibility_input",
                  "product_mix", "productMix", "schedule", "covenants", "legalContext", "source_documents",
                  "activeAssumptionOverrides", "adoptedRestructuringCures"),
    "public_policy": ("project", "land", "landLedger", "landEvidence", "regulation", "regulatoryConstraintSnapshot",
                      "legalContext", "publicContribution", "public_contribution", "policy", "source_documents"),
}
_BOOKKEEPING = {
    "createdAt", "updatedAt", "generatedAt", "timestamp", "reviewId", "legalReviewId", "permitReviewId",
    "handoffId", "artifactKey", "sourceArtifactKey", "runId", "agentRunId", "fetchedAt",
}


def _semantic_output(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _semantic_output(child) for key, child in value.items() if key not in _BOOKKEEPING}
    if isinstance(value, list):
        return [_semantic_output(child) for child in value]
    return value


def _source_projection(project_data: dict[str, Any]) -> dict[str, Any]:
    keys = set().union(*_INPUT_KEYS.values()) | {
        "feasibilityStudyV2Draft", "parsed_facts", "parsedFactCandidates",
        "approvedInputSnapshots", "activeApprovedInputSnapshot", "assumptions",
        "assumptionBook", "inputVersion", "regulatory_constraint_snapshot",
    }
    source = {key: deepcopy(project_data[key]) for key in keys if key in project_data and key != "_meta"}
    metadata = project_data.get("_meta")
    if isinstance(metadata, dict):
        relevant = {key: deepcopy(metadata[key]) for key in
                    ("parsing_source", "feasibilityFieldReviews", "feasibilityTermReviews") if key in metadata}
        if relevant:
            source["_meta"] = relevant
    project = source.get("project")
    if isinstance(project, dict):
        for key in ("updatedAt", "createdAt", "stateVersion", "workflowTrack", "track"):
            project.pop(key, None)
    return source


def source_revision(project_data: dict[str, Any], workflow_track: str | None = None) -> str:
    track = _workflow_track(project_data, {"workflowTrack": workflow_track})
    return stable_digest({"source": _source_projection(project_data), "workflowTrack": track})


def _positive(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return False
    return 0 < number < float("inf")


def _execute_agents(
    project_id: str, pod_key: str, source: dict[str, Any], *, allow_public_lookups: bool,
) -> dict[str, Any]:
    """Only direct existing domain calls run here, with no durable runtime."""
    result: dict[str, Any] = {}
    patch: dict[str, Any] = {}
    executions: list[dict[str, Any]] = []

    def call(agent: str, key: str, execute: Callable[[], Any]) -> Any:
        try:
            output = execute()
        except ValueError:
            executions.append({"agentId": agent, "status": "needs_material", "reason": "input_incomplete"})
            return None
        except Exception:
            executions.append({"agentId": agent, "status": "failed", "reason": "agent_error"})
            return None
        result[key] = output
        executions.append({"agentId": agent, "status": "completed"})
        return output

    if pod_key == "land_legal":
        if allow_public_lookups:
            legal = call("LegalReviewAgent", "legalPod", lambda: run_legal_pod_use_case(
                project_id, deepcopy(source), {"autoEnrichLand": False}, persist=False,
            ))
            if isinstance(legal, dict):
                result["legalPermitReview"] = legal.get("legalReview")
                patch = {key: value for key, value in {
                    "legalPermitReview": legal.get("legalReview"), "buildableEnvelope": legal.get("buildableEnvelope"),
                }.items() if isinstance(value, dict)}
        else:
            legal = call("LegalReviewAgent", "legalPermitReview", lambda: LegalReviewAgent().review(source))
            if isinstance(legal, dict):
                patch["legalPermitReview"] = legal
        result["supportScope"] = "preliminary_land_rights_and_permit_review"
    elif pod_key == "market_product":
        evidence = call("MarketEvidenceAgent", "marketEvidenceReview", lambda: MarketEvidenceAgent().review(source))
        current = deepcopy(source)
        if isinstance(evidence, dict):
            # Archived working inputs may be inspected, but cannot overwrite a saved
            # user value or promote a Parser candidate into an approved input.
            proposed = evidence.get("marketPayloadPatch") or {}
            current = deep_merge(proposed, current)
            current["marketEvidenceReview"] = evidence
            patch["marketEvidenceReview"] = evidence
        market = call("MarketabilityAgent", "marketabilityAnalysis", lambda: analyze_marketability_use_case(current))
        if isinstance(market, dict):
            patch["marketabilityAnalysis"] = market
            call("MarketProductDepthAgent", "marketProductDepth", lambda: MarketProductDepthAgent().analyze(
                current, market, options={"podAiReviewMode": "deterministic"},
            ))
    elif pod_key in {"cost_schedule", "feasibility_cashflow"}:
        basis = development_feasibility_input_basis(source)
        feasibility = call("DevelopmentFeasibilityAgent", "developerFeasibility",
                           lambda: analyze_development_feasibility_use_case(basis))
        # Legacy zero-filled summaries stay labelled as incomplete analysis.
        # Never copy their revenue/cost values into the user's input.
        if isinstance(feasibility, dict):
            if pod_key == "cost_schedule":
                result["costBreakdown"] = feasibility.get("costs") or {}
                result["dataQualityMissingFields"] = (feasibility.get("dataQuality") or {}).get("missingFields") or []
                patch["costSchedule"] = {"costBreakdown": result["costBreakdown"],
                                         "dataQualityMissingFields": result["dataQualityMissingFields"]}
            else:
                patch["developerFeasibility"] = feasibility
        if pod_key == "feasibility_cashflow":
            def cash_flow():
                values = source.get("feasibility_input") or {}
                canonical = source.get("canonicalFeasibilityStudyV2") or source.get("feasibilityStudyV2") or source.get("cashflow_input")
                if not canonical and not (
                    _positive(values.get("total_revenue")) and _positive(values.get("total_cost"))
                    and (source.get("schedule") or source.get("project_schedule"))
                ):
                    raise ValueError("Current cash-flow inputs are missing")
                return execute_cash_flow_request_use_case(build_cash_flow_request_use_case(source))
            cash = call("CashFlowEngineAgent", "cashFlowRunResult", cash_flow)
            if isinstance(cash, dict):
                patch["cashFlowRunResult"] = cash
    elif pod_key == "pf_credit":
        def pf_calculation():
            request = build_pf_structure_input(source)
            rows = request.get("tranches")
            if not isinstance(rows, list) or not rows or not all(
                isinstance(row, dict) and _positive(row.get("amount"))
                and row.get("interest_rate", row.get("rate")) is not None for row in rows
            ):
                raise ValueError("Loan amount and rate need review")
            if not _positive(request.get("total_revenue")) or not _positive(request.get("total_cost")):
                raise ValueError("Current revenue and cost need review")
            return execute_pf_structure_input(request)
        pf = call("PFStructuringAgent", "pfStructure", pf_calculation)
        if isinstance(pf, dict):
            patch["pfStructureAnalysis"] = pf
        artifacts = {key: source[key] for key in
                     ("legalPermitReview", "marketabilityAnalysis", "developerFeasibility", "cashFlowRunResult")
                     if key in source}
        if isinstance(pf, dict):
            artifacts["pfStructure"] = pf
        risk = call("RiskRegisterAgent", "riskRegister", lambda: RiskRegisterAgent().build(source, artifacts))
        if isinstance(risk, dict):
            artifacts["riskRegister"] = risk
            patch["riskRegister"] = risk
        credit = call("CreditReviewAgent", "creditReviewDecision", lambda: CreditReviewAgent().decide(source, artifacts))
        if isinstance(credit, dict):
            patch["creditReviewDecision"] = credit
        call("CovenantEodAgent", "covenantEod", lambda: evaluate_covenant_eod_use_case(source))
    else:
        score = call("DevelopabilityScoreAgent", "developabilityScore", lambda: score_developability_use_case(source))
        if isinstance(score, dict):
            patch["publicPolicyReview"] = {"developabilityScore": score, "analysisOnly": True}
        result["supportScope"] = "existing_developability_review"
        result["unsupportedTasks"] = ["dedicated_policy_simulation", "public_contribution_optimization"]

    review = call("PodAIReviewerAgent", "review", lambda: PodAIReviewerAgent().review_pod(
        project_id=project_id, pod_key=pod_key, source_artifact=result, project_data=source,
        source_artifact_key="preflight:" + stable_digest([project_id, pod_key, source]),
        options={"podAiReviewMode": "deterministic"},
    ))
    findings = review.get("deterministicFindings", []) if isinstance(review, dict) else []
    for execution in executions:
        output = result.get({"DevelopmentFeasibilityAgent": "developerFeasibility",
                             "MarketabilityAgent": "marketabilityAnalysis"}.get(execution["agentId"], ""))
        if isinstance(output, dict) and (
            (output.get("dataQuality") or {}).get("missingFields") or output.get("missingRequiredData")
        ):
            execution["status"] = "needs_material"
            execution["reason"] = "source_values_incomplete"
    result["analysisOnly"] = True
    result["reportEligible"] = False
    status = "failed" if any(row["status"] == "failed" for row in executions) else (
        "needs_material" if any(row["status"] == "needs_material" for row in executions) else "completed"
    )
    return {"result": result, "patch": patch, "agentExecutions": executions, "status": status,
            "findings": findings, "workOrders": []}


def _ancestors(pod_key: str, dependencies: dict[str, list[str]]) -> list[str]:
    result: list[str] = []
    def visit(key: str) -> None:
        for parent in dependencies[key]:
            if parent not in result:
                visit(parent)
                result.append(parent)
    visit(pod_key)
    return result


def _view_for_pod(
    pod_key: str, source: dict[str, Any], dependencies: dict[str, list[str]], state: dict[str, Any],
) -> dict[str, Any]:
    view = {key: deepcopy(source[key]) for key in _INPUT_KEYS[pod_key] if key in source}
    for parent in _ancestors(pod_key, dependencies):
        if (state.get(parent) or {}).get("patch"):
            view.update(deepcopy(state[parent]["patch"]))
    return view


def _input_hash(pod_key: str, source: dict[str, Any], dependencies: list[str], state: dict[str, Any], allow_public: bool) -> str:
    return stable_digest({
        "runnerVersion": RUNNER_VERSION, "source": {key: source[key] for key in _INPUT_KEYS[pod_key] if key in source},
        "upstream": {key: (state.get(key) or {}).get("outputHash") for key in dependencies},
        "publicLookups": allow_public if pod_key == "land_legal" else None,
    })


def _collect_public(
    project_id: str, source: dict[str, Any], previous: dict[str, Any] | None, *,
    retry_failed: bool, deadline: float,
) -> dict[str, Any]:
    identity = stable_digest({
        "parcels": canonical_project_parcel_rows(source), "land": source.get("land"),
        "type": (source.get("project") or {}).get("type"),
        "market": source.get("market"), "landEvidence": source.get("landEvidence"),
    })
    old = previous if isinstance(previous, dict) and previous.get("inputHash") == identity else {}
    attempted = int(old.get("attempts") or 0)
    failed = any(row["status"] == "failed" for row in old.get("lookups") or [])
    deferred = any(row["status"] == "deferred" for row in old.get("lookups") or [])
    if old and (attempted >= 18 or (not deferred and (not failed or not retry_failed or int(old.get("failureAttempts") or 0) >= 3))):
        return deepcopy(old)
    working = deep_merge(source, old.get("patch") or {})
    cadastral_matches = []
    checked, lookup = _confirmed_parcels(
        project_id, working, allow_live=True, cadastral_matches=cadastral_matches,
    )
    lookup["podKey"] = "land_legal"
    lookups = [lookup]
    evidence = deepcopy(working.get("landEvidence") or {})
    rows = list(evidence.get("records") or [])
    for record in cadastral_matches:
        if record not in rows:
            rows.append(record)
    if rows:
        evidence["records"] = rows
        working["landEvidence"] = evidence
    market = working.get("market") or {}
    market_lookup = {"key": "market", "label": "국토교통부 실거래가", "podKey": "market_product"}
    parcels = canonical_project_parcel_rows(working)
    if not parcels and parcel_pnu(working.get("land") or {}):
        parcels = [working["land"]]
    if checked and any(parcel_pnu(row) not in checked for row in parcels) and lookup["status"] == "checked":
        lookup["status"] = "deferred"
        lookup["message"] = "검증된 필지를 유지하며 다음 대상 필지를 이어서 확인합니다."
    districts = {lawd_cd_from_pnu(pnu) for pnu in checked}
    product = str((working.get("project") or {}).get("type") or "").strip().lower()
    asset = ASSET_TYPE_MAP.get(product)
    if market.get("transactionComparables") or market.get("manualComparables"):
        market_lookup.update(status="available", message="저장된 비교자료를 사용했습니다.")
    elif not parcels or any(parcel_pnu(row) not in checked for row in parcels) or len(districts) != 1:
        market_lookup.update(status="needs_input", message="대상 필지와 시군구 확인이 필요합니다.")
    elif not asset or product in {"mixed_use", "주상복합", "serviced_residence", "생활숙박시설", "생숙"}:
        market_lookup.update(status="needs_input", message="비교할 자산 유형의 확인이 필요합니다.")
    elif monotonic() >= deadline:
        market_lookup.update(status="deferred", message="다음 이어가기에서 공개 조회를 확인합니다.")
    else:
        connector = MolitRtmsConnector(http_get=lambda url: _http_get(url, timeout=2.0))
        if not connector.configured():
            market_lookup.update(status="unavailable", message="분석용 실거래 연결 설정을 확인해 주세요.")
        else:
            try:
                records, diagnostic = fetch_project_market_comparables(
                    working, connector=connector, deal_yms=recent_deal_yms(1),
                    lawd_cd=next(iter(districts)), asset_type=asset, per_month_rows=30, max_records=12,
                )
            except Exception:
                records, diagnostic = [], {"status": "failed"}
            if records:
                working["market"] = {**market, "transactionComparables": records}
                market_lookup.update(status="checked", message="해당 지역·유형의 거래를 이번 검토에서 확인했습니다.")
            else:
                market_lookup.update(status="empty" if diagnostic.get("status") == "empty" else "failed",
                                     message="조회 기간의 거래를 확인하지 못했습니다.")
    lookups.append(market_lookup)
    patch = {key: deepcopy(working[key]) for key in
             ("land", "landLedger", "land_ledger", "land_acquisition", "landEvidence", "market")
             if key in working and working.get(key) != source.get(key)}
    failed_now = any(row["status"] == "failed" for row in lookups)
    return {"inputHash": identity, "patch": patch, "lookups": lookups, "attempts": attempted + 1,
            "failureAttempts": int(old.get("failureAttempts") or 0) + 1 if failed_now else 0}


def _draft_review_links(source: dict[str, Any], requests: list[dict[str, Any]]) -> None:
    """A bound workbook draft is a review destination, never a numeric input."""
    draft = source.get("feasibilityStudyV2Draft")
    if not isinstance(draft, dict):
        return
    raw = draft.get("raw_import") or {}
    origin = raw.get("sourceDocument") or draft.get("sourceDocument") or {}
    if not isinstance(origin, dict):
        return
    archive = str(origin.get("archiveId") or origin.get("originalFileHash") or origin.get("sha256") or "")
    documents = source.get("source_documents") or source.get("sourceDocuments") or []
    matching = next((row for row in documents if isinstance(row, dict) and archive and archive in {
        str(row.get("archiveId") or ""), str(row.get("originalFileHash") or ""), str(row.get("sha256") or ""),
    }), None)
    if not matching:
        return
    for item in requests:
        if item.get("itemId") in {"cost_assumption_missing", "revenue_assumption_missing", "pf_structure_missing"}:
            item["action"] = "review_document"
            item["sourceLocators"] = [{
                "sourceType": "uploaded_document", "archiveId": archive,
                "documentName": matching.get("originalFileName") or matching.get("fileName") or "보관 사업수지",
            }]
            item["description"] = "보관한 사업수지 초안의 값과 수식 오류를 원본과 대조해 주세요."


def _agent_requests(
    source: dict[str, Any], pods: list[Any], cached: dict[str, Any], requests: list[dict[str, Any]],
) -> None:
    if any(item["itemId"] == "source_documents_missing" for item in requests):
        return
    by_key = {pod.pod_key: pod for pod in pods}
    for key, record in cached.items():
        if record.get("status") == "failed":
            continue
        result = record.get("result") or {}
        known_requests = [item for item in requests if key in item.get("podKeys", [])]
        descriptions = []
        for finding in record.get("findings") or []:
            if finding.get("severity") in {"blocking", "high"}:
                text = finding.get("recommendedAction") or finding.get("message")
                if text:
                    descriptions.append(str(text))
        legal = result.get("legalPermitReview") or (result.get("legalPod") or {}).get("legalReview") or {}
        descriptions.extend(str(text) for text in legal.get("requiredFollowUps") or [])
        quality = (result.get("developerFeasibility") or {}).get("dataQuality") or {}
        missing = quality.get("missingFields") or (result.get("marketabilityAnalysis") or {}).get("missingRequiredData") or []
        if not (missing or descriptions or record.get("status") == "needs_material"):
            continue
        if known_requests:
            known_requests[0]["agentFollowUps"] = list(dict.fromkeys(descriptions))[:6]
            known_requests[0]["unresolvedFields"] = deepcopy(missing)
            continue
        requests.append({
            "itemId": "agent_input_review_" + key, "category": key, "severity": "high", "requiredFor": [key],
            "description": descriptions[0] if descriptions else "현재 자료에서 이 검토에 필요한 입력을 확인해 주세요.",
            "acceptableSources": by_key[key].required_inputs, "podKeys": [key], "purpose": POD_PURPOSES[key],
            "action": "review_document" if source.get("source_documents") else "provide_material",
            "sourceLocators": [], "agentFollowUps": list(dict.fromkeys(descriptions))[:6],
            "unresolvedFields": deepcopy(missing),
        })


def run_chief_preflight_review(
    project_id: str, project_data: dict[str, Any], *, workflow_track: str | None = None,
    previous_state: dict[str, Any] | None = None, retry_failed: bool = False,
    allow_public_lookups: bool = True, max_rounds: int = 3, max_executions: int = 18,
    time_budget_seconds: float = 20,
) -> dict[str, Any]:
    """Review partial inputs with real Agents and reuse server-attested prior state."""
    started = monotonic()
    deadline = started + max(0.0, time_budget_seconds)
    track = _workflow_track(project_data, {"workflowTrack": workflow_track})
    revision = source_revision(project_data, track)
    source = _source_projection(project_data)
    plans = _agent_pods(_missing_inputs(source), track)
    coordinator = PodExecutionCoordinator()
    order = coordinator.topological_order(plans)
    dependencies = {pod.pod_key: list(pod.dependencies) for pod in plans}
    old = previous_state if isinstance(previous_state, dict) and previous_state.get("runnerVersion") == RUNNER_VERSION \
        and previous_state.get("workflowTrack") == track else {}
    cached = deepcopy(old.get("pods") or {})
    same_revision = old.get("sourceRevision") == revision and old.get("allowPublicLookups") == allow_public_lookups
    total_executions = int(old.get("totalExecutedCount") or 0) if same_revision else 0
    total_rounds = int(old.get("totalRounds") or 0) if same_revision else 0
    max_rounds = max(1, min(int(max_rounds), 3))
    max_executions = max(1, min(int(max_executions), 18))
    queue = deepcopy(old.get("queue") or []) if same_revision else []
    if not queue:
        retry_round = total_rounds + 1 if same_revision and retry_failed else 1
        queue = [{"podKey": key, "round": retry_round} for key in order]
    executed, reused = 0, 0
    rounds: set[int] = set()
    agents: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    attempted: set[tuple[str, str]] = set()
    work_state = {"workOrders": deepcopy(old.get("workOrders") or [])}
    stop = "fixed_point"
    public_state = None
    if allow_public_lookups and monotonic() < deadline:
        public_state = _collect_public(
            project_id, source, old.get("publicState"), retry_failed=retry_failed, deadline=deadline,
        )
        while any(row["status"] == "deferred" for row in public_state["lookups"]) \
                and public_state["attempts"] < 18 and monotonic() < deadline:
            previous_public = public_state
            public_state = _collect_public(
                project_id, source, previous_public, retry_failed=False, deadline=deadline,
            )
            if public_state["patch"] == previous_public["patch"]:
                break
    effective_source = deep_merge(source, (public_state or {}).get("patch") or {})

    while queue:
        item = queue[0]
        key, round_index = item["podKey"], int(item["round"])
        if key not in dependencies:
            queue.pop(0)
            continue
        ancestors = _ancestors(key, dependencies)
        signature = _input_hash(key, effective_source, ancestors, cached, allow_public_lookups)
        previous = cached.get(key) or {}
        unchanged = previous.get("inputHash") == signature
        failed_retry = unchanged and previous.get("status") == "failed" and retry_failed
        if unchanged and not failed_retry:
            reused += 1
            agents.extend({**row, "podKey": key, "round": 0, "reused": True}
                          for row in previous.get("agentExecutions") or [])
            queue.pop(0)
            continue
        if (key, signature) in attempted:
            queue.pop(0)
            continue
        if round_index > max_rounds:
            stop = "round_limit"
            break
        if total_executions >= max_executions:
            stop = "execution_limit"
            break
        if monotonic() >= deadline:
            stop = "time_budget"
            break
        attempts = int(previous.get("attempts") or 0) if unchanged else 0
        if attempts >= max_rounds:
            queue.pop(0)
            continue
        queue.pop(0)
        view = _view_for_pod(key, effective_source, dependencies, cached)
        outcome = _execute_agents(project_id, key, view, allow_public_lookups=allow_public_lookups)
        output_hash = stable_digest(_semantic_output({
            "result": outcome["result"], "patch": outcome["patch"], "status": outcome["status"],
        }))
        row = {**outcome, "inputHash": signature, "outputHash": output_hash, "attempts": attempts + 1}
        cached[key] = row
        attempted.add((key, signature))
        executed += 1
        total_executions += 1
        total_rounds = max(total_rounds, round_index)
        rounds.add(round_index)
        agents.extend({**agent, "podKey": key, "round": round_index, "reused": False}
                      for agent in row["agentExecutions"])
        if previous.get("outputHash") != output_hash:
            for downstream in coordinator.downstream_pods(key, plans):
                envelope = AgentHandoffEnvelope(
                    handoffId="preflight-review:" + stable_digest([revision, key, downstream, output_hash])[:24],
                    workflow="chief-deal-orchestration", handoffType="preflight_agent_result", runId=None,
                    payloadSchema=RUNNER_VERSION, fromAgent=key, toAgent=downstream,
                    status="needs_review" if row["status"] != "completed" else "accepted",
                    payload={"fromPod": key, "toPod": downstream, "analysisOnly": True,
                             "inputHash": signature, "outputHash": output_hash,
                             "basis": "domain_analysis", "reportEligible": False,
                             "acceptedResultKeys": list(row.get("patch") or {})},
                    evidenceIds=list((row["result"].get("review") or {}).get("inputEvidenceIds") or []),
                    createdAt=datetime.now(timezone.utc).isoformat(),
                )
                handoffs.append(envelope.model_dump(by_alias=True))
                if not any(queued["podKey"] == downstream for queued in queue):
                    queue.append({"podKey": downstream, "round": round_index + 1})
        review = outcome["result"].get("review") or {}
        orders = route_review_work_orders(
            review=review,
            artifact={"projectId": project_id, "artifactKey": review.get("sourceArtifactKey"),
                      "inputHash": signature, "outputHash": output_hash},
            missing=_missing_inputs(effective_source), state=work_state,
            now=datetime.now(timezone.utc).isoformat(),
        ) if review else []
        for request in [*orders, *(outcome.get("workOrders") or [])]:
            target = request.get("podKey")
            if request.get("action") == "collect_public_evidence":
                request["status"] = "checked_in_preview" if public_state else "not_enabled"
            if target in dependencies and request.get("action") == "rerun_pod" \
                    and not any(queued["podKey"] == target for queued in queue):
                queue.append({"podKey": target, "round": round_index + 1})

    final = run_chief_pod_preflight(project_id, effective_source, workflow_track=track, allow_public_lookups=False)
    if public_state:
        final["publicLookups"] = public_state["lookups"]
    deferred_public = [lookup for lookup in (public_state or {}).get("lookups") or []
                       if lookup["status"] == "deferred"]
    for lookup in deferred_public:
        if not any(item["podKey"] == lookup["podKey"] for item in queue):
            queue.append({"podKey": lookup["podKey"], "round": max(1, total_rounds + 1)})
    if deferred_public:
        stop = "execution_limit" if public_state["attempts"] >= 18 else "time_budget"
    pending = list(dict.fromkeys(item["podKey"] for item in queue))
    _agent_requests(effective_source, plans, cached, final["materialRequests"])
    _draft_review_links(source, final["materialRequests"])
    for pod in final["pods"]:
        record = cached.get(pod["podKey"]) or {}
        pod["executionStatus"] = "pending" if pod["podKey"] in pending else record.get("status", "pending")
        pod["agentExecutions"] = record.get("agentExecutions") or []
        pod["reviewFindings"] = record.get("findings") or []
        for field in ("unsupportedTasks", "supportScope"):
            if record.get("result", {}).get(field):
                pod[field] = record["result"][field]
    failures = [lookup for lookup in (public_state or {}).get("lookups") or []
                if lookup["status"] == "failed"]
    failed = list(dict.fromkeys([
        *(key for key in order if (cached.get(key) or {}).get("status") == "failed"),
        *(lookup["podKey"] for lookup in failures),
    ]))
    if pending:
        status = "limit_reached"
    elif failed:
        status, stop = "retry_needed", "agent_error" if not failures else "public_lookup_error"
    elif final["materialRequests"]:
        status, stop = "needs_material", "user_material_required"
    else:
        status, stop = "stable", "fixed_point"
    made_progress = executed > 0 or (public_state is not None and public_state != old.get("publicState"))
    can_continue = bool(pending and stop == "time_budget" and made_progress and total_executions < max_executions
                        and min(item["round"] for item in queue) <= max_rounds)
    final["handoffs"].extend(handoffs)
    final["inputRevision"] = revision
    final["reviewLoop"] = {
        "runnerVersion": RUNNER_VERSION, "status": status, "stopReason": stop,
        "executedCount": executed, "reusedCount": reused, "rounds": len(rounds), "agents": agents,
        "totalExecutedCount": total_executions, "totalRounds": total_rounds,
        "executionBudget": max_executions, "roundBudget": max_rounds,
        "pendingPodKeys": pending, "failedPodKeys": failed, "canContinue": can_continue,
        "publicLookupFailures": failures, "materialRequestsFinal": not pending and not failed,
        "canRetryFailed": bool(failed and total_executions < max_executions and total_rounds < max_rounds),
    }
    final["resumeState"] = {
        "runnerVersion": RUNNER_VERSION, "sourceRevision": revision, "workflowTrack": track,
        "allowPublicLookups": allow_public_lookups, "pods": cached, "queue": queue,
        "pendingPodKeys": pending, "totalExecutedCount": total_executions, "totalRounds": total_rounds,
        "publicState": public_state, "workOrders": work_state["workOrders"],
    }
    return final
