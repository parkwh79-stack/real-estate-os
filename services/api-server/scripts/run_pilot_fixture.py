from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from agents.orchestration_agent import OrchestrationAgent  # noqa: E402


DEFAULT_INPUT = API_ROOT / "data" / "fixtures" / "pilot_project_input_package_template.json"


def summarize_run(run: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = run.get("artifacts") or {}
    approval_gate = artifacts.get("approvalReadinessGate") or {}
    credit = artifacts.get("creditReviewDecision") or {}
    risk_register = artifacts.get("riskRegister") or {}
    restructuring = artifacts.get("restructuringPlan") or {}
    land = artifacts.get("landAgentReview") or {}
    evidence_graph = artifacts.get("evidenceGraph") or {}
    quality = artifacts.get("qualityCheck") or {}

    return {
        "schemaVersion": "realytics-pilot-run-summary-v1",
        "runId": run.get("run_id"),
        "projectId": run.get("project_id"),
        "status": run.get("status"),
        "stepCount": len(run.get("steps") or []),
        "completedStepCount": sum(1 for step in run.get("steps") or [] if step.get("status") == "completed"),
        "errorCount": len(run.get("errors") or []),
        "qualityCheck": {
            "pass": quality.get("pass"),
            "gateType": quality.get("gateType"),
            "missingArtifacts": quality.get("missingArtifacts", []),
        },
        "approvalReadinessGate": {
            "pass": approval_gate.get("pass"),
            "status": approval_gate.get("status"),
            "blockingIssueCount": (approval_gate.get("summary") or {}).get("blockingIssueCount"),
            "warningCount": (approval_gate.get("summary") or {}).get("warningCount"),
            "blockers": [
                {
                    "code": item.get("code"),
                    "message": item.get("message"),
                }
                for item in approval_gate.get("blockers", [])
            ],
            "warnings": [
                {
                    "code": item.get("code"),
                    "message": item.get("message"),
                }
                for item in approval_gate.get("warnings", [])
            ],
            "requiredActions": approval_gate.get("requiredActions", []),
        },
        "creditReview": {
            "decision": credit.get("decision"),
            "status": credit.get("status"),
            "conditionCount": len(credit.get("approvalConditions") or []),
            "requiredFollowUpCount": len(credit.get("requiredFollowUps") or []),
        },
        "riskRegister": {
            "status": risk_register.get("status"),
            "riskCount": (risk_register.get("summary") or {}).get("riskCount"),
            "criticalCount": (risk_register.get("summary") or {}).get("criticalCount"),
            "highCount": (risk_register.get("summary") or {}).get("highCount"),
            "humanReviewRequiredCount": (risk_register.get("summary") or {}).get("humanReviewRequiredCount"),
            "topRiskCodes": [
                risk.get("code")
                for risk in (risk_register.get("topRisks") or [])[:8]
            ],
        },
        "land": {
            "status": land.get("status"),
            "handoffReadiness": land.get("handoffReadiness"),
            "evidenceReadiness": land.get("evidenceReadiness", {}),
        },
        "restructuringPlan": {
            "status": restructuring.get("status"),
            "approvalPath": (restructuring.get("approvalPath") or {}).get("currentStatus"),
            "remainingBlockers": (restructuring.get("approvalPath") or {}).get("remainingBlockers", []),
            "recommendations": [
                {
                    "code": item.get("code"),
                    "title": item.get("title"),
                    "amount": item.get("amount"),
                    "alternativeAmount": item.get("alternativeAmount"),
                }
                for item in restructuring.get("recommendations", [])
            ],
            "seniorShortfallFix": restructuring.get("seniorShortfallFix", {}),
            "unsoldLtvFix": restructuring.get("unsoldLtvFix", {}),
        },
        "evidenceGraph": {
            "nodeCount": (evidence_graph.get("summary") or {}).get("nodeCount"),
            "edgeCount": (evidence_graph.get("summary") or {}).get("edgeCount"),
            "riskRowsWithoutExplicitEvidenceCount": (evidence_graph.get("summary") or {}).get(
                "riskRowsWithoutExplicitEvidenceCount"
            ),
        },
        "errors": run.get("errors", []),
    }


def load_payload(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a REALYTICS pilot input package through PF review orchestration.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Pilot input package JSON path.")
    parser.add_argument("--project-id", default="pilot-cli-run", help="Project ID to use for this local run.")
    parser.add_argument("--output", type=Path, help="Optional path for the pilot summary JSON.")
    parser.add_argument("--full-run-output", type=Path, help="Optional path for the complete orchestration run JSON.")
    args = parser.parse_args()

    payload_path = args.input.resolve()
    payload = load_payload(payload_path)
    run = OrchestrationAgent().run_pf_review(args.project_id, payload)
    summary = summarize_run(run)
    summary["inputPath"] = str(payload_path)

    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    print(rendered)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")

    if args.full_run_output:
        args.full_run_output.parent.mkdir(parents=True, exist_ok=True)
        args.full_run_output.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return 0 if run.get("status") in {"completed", "completed_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
