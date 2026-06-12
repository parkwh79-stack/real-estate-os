from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from scripts.build_pilot_blocker_resolution_package import build_resolution_package, render_markdown  # noqa: E402
from use_cases.orchestration_approval import (  # noqa: E402
    review_orchestration_handoff_use_case,
    review_risk_register_row_use_case,
    unresolved_handoff_reviews,
    unresolved_risk_reviews,
)


DEFAULT_FULL_RUN = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "pilot-blocker-full-run.json"
DEFAULT_TEMPLATE = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "pilot-review-actions-template.json"
DEFAULT_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "pilot-reviewed-full-run.json"


RISK_REVIEW_GUIDANCE = {
    "PERMIT_SCHEDULE_READINESS": {
        "recommendedReviewStatus": "needs_revision",
        "requiredEvidence": [
            "permit_schedule",
            "permit_application_receipt",
            "approval_expected_date_evidence",
            "delay_buffer_analysis",
        ],
        "defaultComment": "Attach permit schedule and approval timing evidence before approving this risk.",
    },
    "LEGAL_LAND_RIGHTS": {
        "recommendedReviewStatus": "needs_revision",
        "requiredEvidence": [
            "registry_record",
            "encumbrance_release_condition",
            "ownership_transfer_evidence",
            "trust_registration_evidence",
        ],
        "defaultComment": "Attach registry, encumbrance, ownership transfer, and trust-registration evidence before approving this risk.",
    },
    "COVENANT_AND_LEGAL_PACKAGE_COMPLETENESS": {
        "recommendedReviewStatus": "needs_revision",
        "requiredEvidence": [
            "loan_agreement_draft",
            "trust_agreement",
            "responsible_completion_letter",
            "covenant_eod_checklist",
            "legal_opinion",
        ],
        "defaultComment": "Attach covenant/EOD checklist and legal package evidence before approving this risk.",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or apply source-backed pilot handoff and Risk Register review actions."
    )
    parser.add_argument("--full-run", type=Path, default=DEFAULT_FULL_RUN, help="Full orchestration run JSON.")
    parser.add_argument("--review-actions", type=Path, help="Filled review-action JSON to apply.")
    parser.add_argument("--template-output", type=Path, default=DEFAULT_TEMPLATE, help="Review-action template output.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Reviewed full run JSON output.")
    parser.add_argument("--blocker-package-output", type=Path, help="Optional post-review blocker package JSON output.")
    parser.add_argument("--blocker-package-md-output", type=Path, help="Optional post-review blocker package Markdown output.")
    args = parser.parse_args()

    run = _load(args.full_run)
    if not args.review_actions:
        template = build_review_action_template(run)
        args.template_output.parent.mkdir(parents=True, exist_ok=True)
        args.template_output.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "schemaVersion": "realytics-pilot-review-action-template-output-v1",
            "template": str(args.template_output.resolve()),
            "handoffReviewCount": len(template["handoffReviews"]),
            "riskReviewCount": len(template["riskReviews"]),
        }, ensure_ascii=False, indent=2))
        return 0

    actions = _load(args.review_actions)
    reviewed_run = apply_review_actions(run, actions)
    summary = _review_summary(reviewed_run)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reviewed_run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.blocker_package_output or args.blocker_package_md_output:
        package = build_resolution_package(reviewed_run, {})
        if args.blocker_package_output:
            args.blocker_package_output.parent.mkdir(parents=True, exist_ok=True)
            args.blocker_package_output.write_text(
                json.dumps(package, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if args.blocker_package_md_output:
            args.blocker_package_md_output.parent.mkdir(parents=True, exist_ok=True)
            args.blocker_package_md_output.write_text(render_markdown(package) + "\n", encoding="utf-8")

    print(json.dumps({
        "schemaVersion": "realytics-pilot-review-action-application-v1",
        "input": str(args.full_run.resolve()),
        "actions": str(args.review_actions.resolve()),
        "output": str(args.output.resolve()),
        **summary,
    }, ensure_ascii=False, indent=2))
    return 0


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_review_action_template(run: Dict[str, Any]) -> Dict[str, Any]:
    reviewed_at = datetime.now(timezone.utc).isoformat()
    return {
        "schemaVersion": "realytics-pilot-review-actions-v1",
        "generatedAt": reviewed_at,
        "runId": run.get("run_id"),
        "projectId": run.get("project_id"),
        "reviewPolicy": {
            "nonFabrication": True,
            "approvedOrResolvedRequiresEvidence": True,
            "waivedRequiresWaiverReason": True,
            "needsRevisionDoesNotClearApprovalGate": True,
        },
        "handoffReviews": [
            _handoff_action_template(handoff)
            for handoff in unresolved_handoff_reviews(run)
        ],
        "riskReviews": [
            _risk_action_template(risk)
            for risk in unresolved_risk_reviews(run)
        ],
    }


def _handoff_action_template(handoff: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "handoffId": handoff.get("handoffId"),
        "handoffType": handoff.get("handoffType"),
        "fromAgent": handoff.get("fromAgent"),
        "toAgent": handoff.get("toAgent"),
        "currentStatus": handoff.get("status"),
        "currentReviewStatus": handoff.get("reviewStatus"),
        "recommendedReviewStatus": "needs_revision" if handoff.get("blockingIssues") else "approved",
        "reviewStatus": "",
        "reviewedBy": "credit_officer",
        "comment": _handoff_comment(handoff),
        "evidenceIds": [],
        "sourceLocators": [],
        "blockingIssues": handoff.get("blockingIssues") or [],
        "dataLimitations": handoff.get("dataLimitations") or [],
    }


def _risk_action_template(risk: Dict[str, Any]) -> Dict[str, Any]:
    guidance = RISK_REVIEW_GUIDANCE.get(str(risk.get("code") or ""), {})
    return {
        "riskId": risk.get("riskId"),
        "code": risk.get("code"),
        "category": risk.get("category"),
        "severity": risk.get("severity"),
        "title": risk.get("title"),
        "currentStatus": risk.get("status"),
        "currentReviewStatus": risk.get("reviewStatus"),
        "recommendedReviewStatus": guidance.get("recommendedReviewStatus", "needs_revision"),
        "reviewStatus": "",
        "reviewedBy": "credit_officer",
        "comment": guidance.get("defaultComment", "Attach evidence or waiver rationale before clearing this risk."),
        "waiverReason": "",
        "requiredEvidence": guidance.get("requiredEvidence", []),
        "evidenceIds": [],
        "sourceLocators": [],
        "existingSourceLocators": risk.get("sourceLocators") or [],
        "mitigant": risk.get("mitigant"),
    }


def _handoff_comment(handoff: Dict[str, Any]) -> str:
    issues = handoff.get("blockingIssues") or []
    if issues:
        return "Resolve or evidence the blocking handoff issues before approving this agent handoff."
    return "Handoff reviewed; no blocking issues identified in the current run."


def apply_review_actions(run: Dict[str, Any], actions: Dict[str, Any]) -> Dict[str, Any]:
    reviewed = deepcopy(run)
    reviewed_at = actions.get("reviewedAt") or datetime.now(timezone.utc).isoformat()
    for action in actions.get("handoffReviews") or []:
        review_status = action.get("reviewStatus") or action.get("review_status")
        if not review_status:
            continue
        _validate_handoff_action(reviewed, action, review_status)
        review_orchestration_handoff_use_case(
            reviewed,
            str(action.get("handoffId")),
            {
                "review_status": review_status,
                "reviewed_by": action.get("reviewedBy") or action.get("reviewed_by") or "credit_officer",
                "comment": action.get("comment") or action.get("reviewComment") or "",
            },
            reviewed_at,
        )
        _merge_handoff_review_evidence(reviewed, str(action.get("handoffId")), action)

    for action in actions.get("riskReviews") or []:
        review_status = action.get("reviewStatus") or action.get("review_status")
        if not review_status:
            continue
        _validate_risk_action(reviewed, action, review_status)
        _merge_risk_review_evidence(reviewed, str(action.get("riskId")), action)
        review_risk_register_row_use_case(
            reviewed,
            str(action.get("riskId")),
            {
                "review_status": review_status,
                "reviewed_by": action.get("reviewedBy") or action.get("reviewed_by") or "credit_officer",
                "comment": action.get("comment") or action.get("reviewComment") or "",
                "waiver_reason": action.get("waiverReason") or action.get("waiver_reason"),
            },
            reviewed_at,
        )
    return reviewed


def _validate_handoff_action(run: Dict[str, Any], action: Dict[str, Any], review_status: str) -> None:
    if review_status not in {"approved", "rejected", "needs_revision"}:
        raise ValueError(f"Invalid handoff reviewStatus: {review_status}")
    handoff = _find_handoff(run, str(action.get("handoffId")))
    comment = str(action.get("comment") or action.get("reviewComment") or "").strip()
    if review_status in {"rejected", "needs_revision"} and not comment:
        raise ValueError(f"Handoff {handoff.get('handoffId')} requires a review comment.")
    if review_status == "approved" and handoff.get("blockingIssues") and not _has_action_evidence(action):
        raise ValueError(
            f"Handoff {handoff.get('handoffId')} has blocking issues; approval requires evidenceIds or sourceLocators."
        )


def _validate_risk_action(run: Dict[str, Any], action: Dict[str, Any], review_status: str) -> None:
    if review_status not in {"approved", "waived", "needs_revision", "resolved"}:
        raise ValueError(f"Invalid risk reviewStatus: {review_status}")
    risk = _find_risk(run, str(action.get("riskId")))
    comment = str(action.get("comment") or action.get("reviewComment") or "").strip()
    if review_status in {"approved", "waived", "needs_revision", "resolved"} and not comment:
        raise ValueError(f"Risk {risk.get('riskId')} requires a review comment.")
    if review_status == "waived" and not str(action.get("waiverReason") or action.get("waiver_reason") or "").strip():
        raise ValueError(f"Risk {risk.get('riskId')} waiver requires waiverReason.")
    if review_status in {"approved", "resolved"} and not _has_action_evidence(action):
        raise ValueError(f"Risk {risk.get('riskId')} approval/resolution requires evidenceIds or sourceLocators.")
    if review_status == "waived" and not (_has_action_evidence(action) or risk.get("sourceLocators")):
        raise ValueError(f"Risk {risk.get('riskId')} waiver requires evidence/source locator context.")


def _has_action_evidence(action: Dict[str, Any]) -> bool:
    return bool(action.get("evidenceIds") or action.get("sourceLocators") or action.get("sourceUrlOrEvidenceId"))


def _merge_handoff_review_evidence(run: Dict[str, Any], handoff_id: str, action: Dict[str, Any]) -> None:
    handoff = _find_handoff(run, handoff_id)
    evidence_ids = list(dict.fromkeys([*(handoff.get("evidenceIds") or []), *(action.get("evidenceIds") or [])]))
    handoff["evidenceIds"] = evidence_ids
    if action.get("sourceLocators"):
        handoff["sourceLocators"] = [*(handoff.get("sourceLocators") or []), *action["sourceLocators"]]
    if action.get("sourceUrlOrEvidenceId"):
        handoff.setdefault("evidenceIds", [])
        if action["sourceUrlOrEvidenceId"] not in handoff["evidenceIds"]:
            handoff["evidenceIds"].append(action["sourceUrlOrEvidenceId"])


def _merge_risk_review_evidence(run: Dict[str, Any], risk_id: str, action: Dict[str, Any]) -> None:
    risk = _find_risk(run, risk_id)
    evidence_ids = list(dict.fromkeys([*(risk.get("evidenceIds") or []), *(action.get("evidenceIds") or [])]))
    if action.get("sourceUrlOrEvidenceId") and action["sourceUrlOrEvidenceId"] not in evidence_ids:
        evidence_ids.append(action["sourceUrlOrEvidenceId"])
    risk["evidenceIds"] = evidence_ids
    if action.get("sourceLocators"):
        risk["sourceLocators"] = [*(risk.get("sourceLocators") or []), *action["sourceLocators"]]
    risk.setdefault("reviewEvidence", [])
    if _has_action_evidence(action):
        risk["reviewEvidence"].append({
            "evidenceIds": action.get("evidenceIds") or [],
            "sourceUrlOrEvidenceId": action.get("sourceUrlOrEvidenceId"),
            "sourceLocators": action.get("sourceLocators") or [],
        })


def _find_handoff(run: Dict[str, Any], handoff_id: str) -> Dict[str, Any]:
    handoff = next((item for item in run.get("handoffs", []) if item.get("handoffId") == handoff_id), None)
    if not handoff:
        raise LookupError(f"Agent handoff not found: {handoff_id}")
    return handoff


def _find_risk(run: Dict[str, Any], risk_id: str) -> Dict[str, Any]:
    risk_register = run.get("artifacts", {}).get("riskRegister") or {}
    risk = next((item for item in risk_register.get("risks", []) if item.get("riskId") == risk_id), None)
    if not risk:
        raise LookupError(f"Risk register row not found: {risk_id}")
    return risk


def _review_summary(run: Dict[str, Any]) -> Dict[str, Any]:
    gate = (run.get("artifacts") or {}).get("approvalReadinessGate") or {}
    return {
        "approvalReadinessAfter": {
            "status": gate.get("status"),
            "blockers": [item.get("code") for item in gate.get("blockers") or []],
            "warnings": [item.get("code") for item in gate.get("warnings") or []],
        },
        "unresolvedHandoffReviewCount": len(unresolved_handoff_reviews(run)),
        "unresolvedRiskReviewCount": len(unresolved_risk_reviews(run)),
    }


if __name__ == "__main__":
    raise SystemExit(main())
