"""Translate reviewer proposals into bounded, existing Pod operations.

The model selects a destination and an action; it cannot supply calculation
inputs, code, URLs, approvals, or changes to the execution graph.
"""
from copy import deepcopy

from schemas.orchestration import AgentHandoffEnvelope
from use_cases.chief_pod_records import stable_digest


POD_EXECUTION_AGENTS = {
    "land_legal": ["LegalReviewAgent", "LandLedgerEnrichmentAgent", "DevelopabilityScoreAgent"],
    "market_product": ["MarketEvidenceAgent", "MarketabilityAgent", "ProductPlanningAgent", "MarketProductDepthAgent"],
    "cost_schedule": ["DevelopmentFeasibilityAgent"],
    "feasibility_cashflow": ["DevelopmentFeasibilityAgent", "CashFlowEngineAgent"],
    "pf_credit": ["PFStructuringAgent", "ScenarioEngineAgent", "RiskRegisterAgent", "CreditReviewAgent", "CovenantEodAgent"],
    "public_policy": ["PublicFeasibilityAgent"],
}
PUBLIC_INPUTS = {
    "pnu_or_land_ledger_missing": "land_legal",
    "land_evidence_missing": "land_legal",
    "market_evidence_missing": "market_product",
}


def missing_input_work_plan(missing):
    """Explicit material requests for every affected Pod, including partial analyses."""
    rows = []
    for item in missing:
        for pod in POD_EXECUTION_AGENTS:
            if pod not in item.required_for and "all_pods" not in item.required_for:
                continue
            auto_pod = PUBLIC_INPUTS.get(item.item_id)
            rows.append({
                "itemId": item.item_id, "podKey": pod,
                "agents": POD_EXECUTION_AGENTS[pod], "description": item.description,
                "acceptableSources": item.acceptable_sources,
                "action": "collect_public_evidence" if auto_pod else "request_material",
                "collectionPod": auto_pod,
                "limitation": "공개 자료로 확인 가능한 값만 보강하며 등기·약정·최종 승인 증빙은 별도 확인합니다."
                    if auto_pod else "보유 자료 또는 명시적인 사용자 가정이 필요합니다.",
            })
    return rows


def route_review_work_orders(*, review, artifact, missing, state, now, handoff_sink=None):
    """Persist proposals, return newly dispatchable orders; duplicate work is not retried."""
    orders = state.setdefault("workOrders", [])
    existing = {row["workOrderId"] for row in orders}
    missing_by_id = {item.item_id: item for item in missing}
    dispatched = []
    proposals = list(review.get("handoffs") or [])
    if review.get("reviewMode") == "deterministic_fallback_review":
        for item in missing:
            owner = PUBLIC_INPUTS.get(item.item_id)
            if owner and review.get("podKey") in item.required_for:
                proposals.append({"toAgent": owner, "handoffType": "work_order", "decisionBasis": "missing_input_register",
                                  "payload": {"action": "collect_public_evidence", "missingItemId": item.item_id,
                                              "reason": item.description}})
    for proposal in proposals:
        payload = proposal.get("payload") or {}
        target = str(proposal.get("toAgent") or "")
        requested_pod = payload.get("podKey")
        candidates = [pod for pod, agents in POD_EXECUTION_AGENTS.items() if target == pod or target in agents]
        pod = ((requested_pod if requested_pod in candidates else None) if requested_pod is not None
               else candidates[0] if len(candidates) == 1 else None)
        action = payload.get("action") or ("rerun_pod" if proposal.get("handoffType") == "recalculation" else "request_material")
        item_id = payload.get("missingItemId")
        identity = {"sourcePod": review.get("podKey"), "sourceOutputHash": artifact.get("outputHash"),
                    "sourceInputHash": artifact.get("inputHash"), "target": pod or target, "pod": pod, "action": action, "item": item_id}
        order_id = "pod-work:" + stable_digest(identity)[:24]
        if order_id in existing:
            continue
        status, reason = "needs_review", "담당 Pod와 실행 내용을 확인해야 합니다."
        bound = (review.get("sourceArtifactKey") == artifact.get("artifactKey")
                 and review.get("projectId") == artifact.get("projectId")
                 and bool(artifact.get("artifactKey")) and bool(artifact.get("outputHash")))
        allowed_payload = set(payload) <= {"action", "missingItemId", "reason", "podKey"}
        if bound and pod and allowed_payload and proposal.get("handoffType") in {"work_order", "recalculation", "review_request"}:
            if action == "rerun_pod":
                status, reason = "queued", "저장된 입력으로 기존 Pod를 다시 실행하고 후속 영향을 검토합니다."
            elif action == "collect_public_evidence" and PUBLIC_INPUTS.get(item_id) == pod and item_id in missing_by_id:
                status, reason = "queued", "기존 공개자료 연결기로 보강 후 해당 Pod를 다시 검토합니다."
            elif action == "request_material":
                status, reason = "needs_material", "자동으로 확정할 수 없는 자료 또는 사용자 가정을 확인해야 합니다."
        row = {
            "workOrderId": order_id, "projectId": review.get("projectId"),
            "sourceReviewId": review.get("reviewId"), "sourceArtifactKey": artifact.get("artifactKey"),
            "sourcePod": review.get("podKey"), "reviewMode": review.get("reviewMode"),
            "decisionBasis": proposal.get("decisionBasis") or "review_handoff",
            "podKey": pod, "toAgent": target, "action": action, "missingItemId": item_id,
            "reason": reason, "requestedReason": str(payload.get("reason") or ""),
            "status": status, "createdAt": now, "updatedAt": now,
            "evidenceIds": deepcopy(review.get("inputEvidenceIds") or []),
        }
        envelope = AgentHandoffEnvelope(
            handoffId=order_id, workflow="chief-deal-orchestration", runId=review.get("runId"),
            handoffType="pod_review_work_order", fromAgent=review.get("agentId") or "PodAIReviewerAgent",
            toAgent=target or "ChiefDealOrchestrator", status="accepted" if status == "queued" else "needs_review",
            payloadSchema="realytics-pod-work-order-v1", payload=deepcopy(row),
            evidenceIds=row["evidenceIds"], requiresHumanReview=status != "queued", createdAt=now,
        ).model_dump(by_alias=True)
        if handoff_sink:
            handoff_sink(envelope)
        orders.append(row)
        existing.add(order_id)
        if status == "queued":
            dispatched.append(row)
    return dispatched


def finish_work_orders(orders, *, now, status, artifact_key=None, reason=None):
    for order in orders:
        order.update(status=status, updatedAt=now)
        if artifact_key:
            order["resultArtifactKey"] = artifact_key
        if reason:
            order["reason"] = reason
