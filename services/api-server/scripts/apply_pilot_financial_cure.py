from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from agents.orchestration_agent import OrchestrationAgent  # noqa: E402
from scripts.run_pilot_fixture import summarize_run  # noqa: E402


DEFAULT_INPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "pilot_input_with_dq_overrides.json"
DEFAULT_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "pilot_input_with_financial_cure.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a source-backed financial cure to a pilot payload and validate rerun.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Pilot input JSON path.")
    parser.add_argument("--full-run", type=Path, help="Prior full run JSON used to infer required cure amount.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Patched pilot payload JSON output.")
    parser.add_argument(
        "--cure-type",
        default="additional_collateral",
        choices=["additional_collateral", "pf_reduction"],
        help="Financial cure type to apply.",
    )
    parser.add_argument("--amount", type=float, help="Cure amount. If omitted, inferred from restructuring plan.")
    parser.add_argument("--evidence-id", default="pilot-financial-cure-evidence-001", help="Evidence ID or URL for the cure.")
    parser.add_argument("--reviewed-by", default="credit_officer", help="Reviewer/user recording the cure.")
    parser.add_argument("--project-id", default="pilot-financial-cure", help="Project ID for validation rerun.")
    parser.add_argument("--summary-output", type=Path, help="Optional validation run summary JSON output.")
    parser.add_argument("--full-run-output", type=Path, help="Optional validation full run JSON output.")
    args = parser.parse_args()

    payload = _load(args.input)
    prior_run = _load(args.full_run) if args.full_run else OrchestrationAgent().run_pf_review(f"{args.project_id}-baseline", payload)
    amount = args.amount if args.amount is not None else _infer_amount(prior_run, args.cure_type)
    if amount <= 0:
        raise SystemExit(f"No positive cure amount could be inferred for {args.cure_type}.")

    patched = _apply_cure(
        payload,
        prior_run=prior_run,
        cure_type=args.cure_type,
        amount=amount,
        evidence_id=args.evidence_id,
        reviewed_by=args.reviewed_by,
    )
    validation_run = OrchestrationAgent().run_pf_review(args.project_id, patched)
    summary = summarize_run(validation_run)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(patched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.full_run_output:
        args.full_run_output.parent.mkdir(parents=True, exist_ok=True)
        args.full_run_output.write_text(json.dumps(validation_run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = {
        "schemaVersion": "realytics-pilot-financial-cure-application-v1",
        "input": str(args.input.resolve()),
        "output": str(args.output.resolve()),
        "cureType": args.cure_type,
        "amount": amount,
        "evidenceId": args.evidence_id,
        "unsoldLtvSummary": ((validation_run.get("artifacts") or {}).get("scenariosEvaluation") or {})
        .get("unsoldLtvMatrix", {})
        .get("summary", {}),
        "scenarioRiskCodes": [
            flag.get("code")
            for flag in ((validation_run.get("artifacts") or {}).get("scenariosEvaluation") or {}).get("riskFlags", [])
        ],
        "approvalReadinessAfter": {
            "status": ((validation_run.get("artifacts") or {}).get("approvalReadinessGate") or {}).get("status"),
            "blockers": [
                item.get("code")
                for item in ((validation_run.get("artifacts") or {}).get("approvalReadinessGate") or {}).get("blockers", [])
            ],
            "warnings": [
                item.get("code")
                for item in ((validation_run.get("artifacts") or {}).get("approvalReadinessGate") or {}).get("warnings", [])
            ],
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _infer_amount(run: Dict[str, Any], cure_type: str) -> float:
    unsold = ((run.get("artifacts") or {}).get("restructuringPlan") or {}).get("unsoldLtvFix") or {}
    if cure_type == "additional_collateral":
        return float(unsold.get("requiredAdditionalCollateralValue") or 0.0)
    return float(unsold.get("requiredPfReductionOrEquityIncrease") or 0.0)


def _apply_cure(
    payload: Dict[str, Any],
    *,
    prior_run: Dict[str, Any],
    cure_type: str,
    amount: float,
    evidence_id: str,
    reviewed_by: str,
) -> Dict[str, Any]:
    patched = deepcopy(payload)
    source_locators = (
        ((prior_run.get("artifacts") or {}).get("restructuringPlan") or {})
        .get("unsoldLtvFix", {})
        .get("sourceLocators", [])
    )
    cure_record = {
        "cureId": f"cure-pilot-unsold-ltv-{cure_type.replace('_', '-')}-{int(round(amount))}",
        "cureType": cure_type,
        "status": "active",
        "amount": amount,
        "riskCodes": ["UNSOLD_LTV_BREACH"],
        "reviewedBy": reviewed_by,
        "reviewedAt": datetime.now(timezone.utc).isoformat(),
        "description": _description(cure_type),
        "sourceUrlOrEvidenceId": evidence_id,
        "evidenceIds": [evidence_id],
        "sourceLocators": source_locators,
    }
    patched["adoptedRestructuringCures"] = [cure_record]
    if cure_type == "additional_collateral":
        drivers = patched.get("scenarioDrivers") or patched.get("scenario_drivers") or {}
        drivers["additionalCollateralValue"] = amount
        patched["scenarioDrivers"] = drivers
    elif cure_type == "pf_reduction":
        _reduce_pf_structure(patched, amount)
    return patched


def _reduce_pf_structure(payload: Dict[str, Any], amount: float) -> None:
    remaining = amount
    tranches = payload.get("pf_structure") or payload.get("pfStructure") or []
    for tranche in sorted(tranches, key=lambda item: item.get("priority", 999), reverse=True):
        current = float(tranche.get("amount") or 0.0)
        reduction = min(current, remaining)
        tranche["originalAmountBeforeCure"] = current
        tranche["amount"] = round(current - reduction, 0)
        tranche["cureReductionAmount"] = round(reduction, 0)
        remaining -= reduction
        if remaining <= 0:
            break
    if remaining > 0:
        raise SystemExit(f"PF reduction amount exceeds total PF structure by {remaining:,.0f}.")


def _description(cure_type: str) -> str:
    if cure_type == "additional_collateral":
        return (
            "Attach additional collateral, forward-sale, bulk-sale, or refinancing value required by the "
            "Restructuring Agent to bring worst-cell Unsold LTV to the target without reducing PF exposure."
        )
    return "Reduce total PF exposure by the amount required by the Restructuring Agent."


if __name__ == "__main__":
    raise SystemExit(main())
