"""Draft download is not approval. Approved-purpose export fails closed."""
from typing import Any, Callable
import re


def evaluate_cashflow_export_policy(
    runs: dict[str, dict[str, Any]],
    purpose: str,
    load_gate: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    if purpose not in {"draft", "approved"}:
        raise ValueError("invalid_export_purpose")
    if purpose == "draft":
        return {"exportPurpose": "draft", "downloadEligible": True, "reportEligible": False,
                "approvalStatus": "not_evaluated", "notice": "검토용 초안 — 승인·투자의사결정용 자료가 아닙니다."}
    try:
        gate = load_gate()
    except Exception:
        gate = {"pass": False}
    anchor = runs.get("cash_flow_engine") or {}
    result = anchor.get("resultSnapshot") or {}
    evidence_bound = bool(anchor.get("inputEvidenceIds"))
    digest = anchor.get("financialInputHash")
    lineage_bound = bool(isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest)) and all(
        row.get("financialInputHash") == digest for row in runs.values()
    )
    eligible = gate.get("pass") is True and result.get("reportEligible") is True and evidence_bound and lineage_bound
    return {"exportPurpose": "approved", "downloadEligible": eligible, "reportEligible": eligible,
            "approvalStatus": "ready" if eligible else "not_ready",
            "notice": "승인 보고서용 계산자료" if eligible else "최신 근거·계산·승인 Gate 검증이 필요합니다.",
            "failureCodes": [code for code, passed in (
                ("approval_readiness_required", gate.get("pass") is True),
                ("cashflow_report_eligibility_required", result.get("reportEligible") is True),
                ("cashflow_input_evidence_required", evidence_bound),
                ("financial_input_lineage_required", lineage_bound),
            ) if not passed]}
