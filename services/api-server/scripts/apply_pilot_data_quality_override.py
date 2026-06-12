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

from agents.orchestration_agent import OrchestrationAgent  # noqa: E402
from scripts.run_pilot_fixture import summarize_run  # noqa: E402


DEFAULT_INPUT = API_ROOT / "data" / "fixtures" / "pilot_project_input_package_template.json"
DEFAULT_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "pilot_input_with_dq_overrides.json"

DEFAULT_CHECK_SOURCE = {
    "unit_count_consistency": "product_mix.units.sum",
}

DEFAULT_CANONICAL_FIELD = {
    "unit_count_consistency": "architectural_plan.units",
}

DEFAULT_REASON = {
    "unit_count_consistency": (
        "Product mix schedule is the authoritative unit basis for the pilot feasibility and sales revenue model; "
        "architectural plan unit count remains visible as a conflicting source."
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply approved Data Quality assumption overrides to a pilot payload.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Pilot input package JSON path.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Patched pilot payload JSON output.")
    parser.add_argument("--check-key", default="unit_count_consistency", help="Data Quality check key to override.")
    parser.add_argument(
        "--approved-source",
        help="Source name from the Data Quality check metadata to use as the approved value.",
    )
    parser.add_argument("--approved-value", type=float, help="Approved value. If omitted, value is read from approved source.")
    parser.add_argument("--canonical-field", help="Canonical field represented by the approved value.")
    parser.add_argument("--approved-by", default="credit_officer", help="Reviewer/user approving the override.")
    parser.add_argument("--reason", help="Approval reason.")
    parser.add_argument("--project-id", default="pilot-dq-override", help="Project ID for the validation rerun.")
    parser.add_argument("--summary-output", type=Path, help="Optional validation run summary JSON output.")
    parser.add_argument("--full-run-output", type=Path, help="Optional validation full run JSON output.")
    args = parser.parse_args()

    payload = _load(args.input)
    baseline_run = OrchestrationAgent().run_pf_review(f"{args.project_id}-baseline", payload)
    check = _find_check(baseline_run, args.check_key)
    source_name = args.approved_source or DEFAULT_CHECK_SOURCE.get(args.check_key)
    approved_value = args.approved_value
    source_value = _find_source_value(check, source_name) if source_name else None
    if approved_value is None:
        if source_value is None:
            raise SystemExit(f"Could not infer approved value for check {args.check_key!r}.")
        approved_value = float(source_value.get("value"))

    override = _build_override(
        check=check,
        check_key=args.check_key,
        canonical_field=args.canonical_field or DEFAULT_CANONICAL_FIELD.get(args.check_key) or args.check_key,
        approved_value=approved_value,
        approved_source=source_name,
        source_value=source_value,
        approved_by=args.approved_by,
        reason=args.reason or DEFAULT_REASON.get(args.check_key) or "Reviewer selected an authoritative source value.",
    )
    patched = _upsert_override(payload, override)

    validation_run = OrchestrationAgent().run_pf_review(args.project_id, patched)
    summary = summarize_run(validation_run)
    result = {
        "schemaVersion": "realytics-pilot-dq-override-application-v1",
        "input": str(args.input.resolve()),
        "output": str(args.output.resolve()),
        "checkKey": args.check_key,
        "approvedSource": source_name,
        "approvedValue": approved_value,
        "overrideId": override["overrideId"],
        "before": _dq_status(baseline_run),
        "after": _dq_status(validation_run),
        "approvalReadinessAfter": {
            "status": (validation_run.get("artifacts", {}).get("approvalReadinessGate") or {}).get("status"),
            "blockers": [
                item.get("code")
                for item in (validation_run.get("artifacts", {}).get("approvalReadinessGate") or {}).get("blockers", [])
            ],
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(patched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.full_run_output:
        args.full_run_output.parent.mkdir(parents=True, exist_ok=True)
        args.full_run_output.write_text(json.dumps(validation_run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_check(run: Dict[str, Any], check_key: str) -> Dict[str, Any]:
    data_quality = (run.get("artifacts") or {}).get("dataQualityReview") or {}
    for check in data_quality.get("checks") or []:
        if check.get("key") == check_key:
            return check
    raise SystemExit(f"Data Quality check not found: {check_key}")


def _find_source_value(check: Dict[str, Any], source_name: str | None) -> Dict[str, Any] | None:
    if not source_name:
        return None
    metadata = check.get("metadata") if isinstance(check.get("metadata"), dict) else {}
    for value in metadata.get("values") or []:
        if value.get("source") == source_name:
            return value
    return None


def _build_override(
    *,
    check: Dict[str, Any],
    check_key: str,
    canonical_field: str,
    approved_value: float,
    approved_source: str | None,
    source_value: Dict[str, Any] | None,
    approved_by: str,
    reason: str,
) -> Dict[str, Any]:
    unit = (check.get("metadata") or {}).get("unit") if isinstance(check.get("metadata"), dict) else None
    override_id = f"ovr-pilot-{check_key.replace('_', '-')}-{_slug_value(approved_value)}"
    source_locator = {
        "sourceType": "data_quality_source_value",
        "source": approved_source or "manual_approved_value",
        "path": f"dataQualityReview.checks[key={check_key}].metadata.values",
        "fieldName": canonical_field,
        "value": approved_value,
        "locatorPrecision": "data_quality_value",
    }
    if source_value:
        source_locator["sourceValue"] = source_value
    return {
        "overrideId": override_id,
        "checkKey": check_key,
        "canonicalField": canonical_field,
        "approvedValue": approved_value,
        "unit": unit,
        "status": "active",
        "approvedBy": approved_by,
        "approvedAt": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "evidenceIds": [f"data-quality:{check_key}:{approved_source or 'manual'}"],
        "sourceLocators": [source_locator],
    }


def _upsert_override(payload: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    patched = deepcopy(payload)
    existing = patched.get("approvedAssumptionOverrides") or patched.get("approved_assumption_overrides") or []
    records: List[Dict[str, Any]] = []
    for item in existing:
        if not isinstance(item, dict):
            continue
        if item.get("checkKey") == override["checkKey"] and item.get("canonicalField") == override["canonicalField"]:
            records.append({**item, "status": "superseded", "effectiveStatus": "superseded"})
            continue
        records.append(item)
    records.append(override)
    patched["approvedAssumptionOverrides"] = records
    return patched


def _dq_status(run: Dict[str, Any]) -> Dict[str, Any]:
    data_quality = (run.get("artifacts") or {}).get("dataQualityReview") or {}
    return {
        "status": data_quality.get("status"),
        "summary": data_quality.get("summary"),
        "appliedOverrideIds": [item.get("overrideId") for item in data_quality.get("appliedOverrides") or []],
    }


def _slug_value(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).replace(".", "-").replace(" ", "-")


if __name__ == "__main__":
    raise SystemExit(main())
