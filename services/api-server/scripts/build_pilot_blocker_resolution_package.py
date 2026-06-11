from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FULL_RUN = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "pilot-blocker-full-run.json"
DEFAULT_LAND_REQUEST = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "land-evidence-request.json"
DEFAULT_JSON_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "pilot-blocker-resolution-package.json"
DEFAULT_MD_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "pilot-blocker-resolution-package.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a pilot approval blocker resolution package.")
    parser.add_argument("--full-run", type=Path, default=DEFAULT_FULL_RUN, help="Full orchestration run JSON.")
    parser.add_argument("--land-request", type=Path, default=DEFAULT_LAND_REQUEST, help="Land evidence request JSON.")
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON_OUTPUT, help="Resolution package JSON output.")
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT, help="Resolution package markdown output.")
    args = parser.parse_args()

    run = json.loads(args.full_run.read_text(encoding="utf-8"))
    land_request = json.loads(args.land_request.read_text(encoding="utf-8")) if args.land_request.exists() else {}
    package = build_resolution_package(run, land_request)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.md_output.write_text(render_markdown(package) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "json": str(args.output.resolve()),
                "markdown": str(args.md_output.resolve()),
                "blockingIssueCount": package["summary"]["blockingIssueCount"],
                "landEvidenceMissingCount": package["landEvidencePlan"]["totalMissingEvidenceCount"],
                "unresolvedRiskReviewCount": package["reviewPlan"]["unresolvedRiskReviewCount"],
                "unresolvedHandoffReviewCount": package["reviewPlan"]["unresolvedHandoffReviewCount"],
                "unsoldLtvCureAmount": package["financialCurePlan"]["unsoldLtvFix"].get(
                    "requiredPfReductionOrEquityIncrease"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_resolution_package(run: Dict[str, Any], land_request: Dict[str, Any] | None = None) -> Dict[str, Any]:
    artifacts = run.get("artifacts") or {}
    approval_gate = artifacts.get("approvalReadinessGate") or {}
    risk_register = artifacts.get("riskRegister") or {}
    restructuring = artifacts.get("restructuringPlan") or {}
    credit = artifacts.get("creditReviewDecision") or {}
    land_request = land_request or {}

    blockers = approval_gate.get("blockers") or []
    warnings = approval_gate.get("warnings") or []

    package = {
        "schemaVersion": "realytics-pilot-blocker-resolution-package-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "runId": run.get("run_id"),
        "projectId": run.get("project_id"),
        "summary": {
            "approvalReady": bool(approval_gate.get("pass")),
            "approvalStatus": approval_gate.get("status"),
            "creditDecision": credit.get("decision"),
            "riskRegisterStatus": risk_register.get("status"),
            "blockingIssueCount": len(blockers),
            "warningCount": len(warnings),
            "runErrorCount": len(run.get("errors") or []),
        },
        "blockers": _blocker_actions(blockers, warnings),
        "landEvidencePlan": _land_evidence_plan(land_request),
        "financialCurePlan": _financial_cure_plan(restructuring),
        "reviewPlan": _review_plan(run, risk_register),
        "rerunPlan": _rerun_plan(),
        "nonFabricationPolicy": [
            "Do not fill official land price, cadastral, or registry evidence without source name and evidence ID/URL.",
            "Do not waive land evidence blockers merely because PNU was generated.",
            "Do not treat restructuring cure as complete until PF reduction, equity, DSRA, collateral, forward-sale, bulk-sale, or refinancing evidence is attached and scenarios are rerun.",
            "Do not change Credit Review from rework/reject to approval without clearing or formally waiving material Risk Register and handoff reviews.",
        ],
    }
    return package


def _blocker_actions(blockers: List[Dict[str, Any]], warnings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    action_map = {
        "RISK_REGISTER_NOT_BLOCKING": {
            "owner": "심사역 / Risk Register reviewer",
            "action": "Critical/high risk rows must be approved, waived with reason, marked needs_revision, or resolved.",
            "target": "Risk Register review workflow",
        },
        "CREDIT_DECISION_NOT_REJECT_OR_REWORK": {
            "owner": "Credit Review Agent / 심사역",
            "action": "Attach cure evidence and rerun Credit Review until decision becomes conditional_approval or approve.",
            "target": "Credit Review rerun",
        },
        "HANDOFF_REVIEWS_RESOLVED": {
            "owner": "Agent owner reviewers",
            "action": "Approve or mark needs_revision for each pending handoff.",
            "target": "Orchestration handoff review panel",
        },
        "RISK_REVIEWS_RESOLVED": {
            "owner": "Risk owner reviewers",
            "action": "Review all material risk rows and record reviewer/comment/waiver reason where applicable.",
            "target": "Risk Register review panel",
        },
        "LAND_OFFICIAL_PRICE_READY": {
            "owner": "Land Agent / 감정·토지 담당",
            "action": "Attach parcel-level official land price evidence with base year/date, price per sqm, source, and evidence ID/URL.",
            "target": "land-evidence-template.json",
        },
        "LAND_REGISTRY_EVIDENCE_READY": {
            "owner": "Legal / 등기 담당",
            "action": "Attach registry and encumbrance evidence for each parcel, including owner match and release conditions.",
            "target": "land-evidence-template.json",
        },
        "UNSOLD_LTV_RESTRUCTURING_REQUIRED": {
            "owner": "PF structuring / 대주단",
            "action": "Reduce PF exposure, increase equity/DSRA, add collateral, or attach forward-sale/bulk-sale/refinancing evidence.",
            "target": "Restructuring plan and scenario rerun",
        },
    }

    rows = []
    for item in [*blockers, *warnings]:
        code = item.get("code")
        action = action_map.get(code, {})
        rows.append(
            {
                "code": code,
                "message": item.get("message"),
                "severity": "blocking" if item in blockers else "warning",
                "owner": action.get("owner", "Review owner"),
                "requiredAction": action.get("action", item.get("message")),
                "targetArtifactOrScreen": action.get("target", "Approval Readiness Dashboard"),
            }
        )
    return rows


def _land_evidence_plan(land_request: Dict[str, Any]) -> Dict[str, Any]:
    summary = land_request.get("summary") or {}
    parcel_rows = land_request.get("parcelEvidenceRequests") or []
    missing_rows = []
    for row in parcel_rows:
        missing = [key for key, value in (row.get("missingEvidence") or {}).items() if value]
        if missing:
            missing_rows.append(
                {
                    "parcelId": row.get("parcelId"),
                    "pnu": row.get("pnu"),
                    "addressFragment": row.get("addressFragment"),
                    "areaSqm": row.get("areaSqm"),
                    "totalContractAmount": row.get("totalContractAmount"),
                    "missingEvidence": missing,
                    "sourceLocators": row.get("sourceLocators") or [],
                }
            )
    return {
        "parcelCount": summary.get("parcelCount", len(parcel_rows)),
        "pnuReadyCount": summary.get("pnuReadyCount"),
        "officialLandPriceMissingCount": summary.get("officialLandPriceMissingCount"),
        "registryEvidenceMissingCount": summary.get("registryEvidenceMissingCount"),
        "cadastralCharacteristicsMissingCount": summary.get("cadastralCharacteristicsMissingCount"),
        "totalMissingEvidenceCount": sum(len(row["missingEvidence"]) for row in missing_rows),
        "requiredTemplate": "land-evidence-template.json",
        "attachmentScript": "services/api-server/scripts/apply_land_evidence_to_pilot.py",
        "missingParcelRows": missing_rows,
        "requiredFields": land_request.get("requiredEvidencePolicy") or [],
    }


def _financial_cure_plan(restructuring: Dict[str, Any]) -> Dict[str, Any]:
    senior = restructuring.get("seniorShortfallFix") or {}
    unsold = restructuring.get("unsoldLtvFix") or {}
    return {
        "status": restructuring.get("status"),
        "approvalPath": (restructuring.get("approvalPath") or {}).get("currentStatus"),
        "remainingBlockers": (restructuring.get("approvalPath") or {}).get("remainingBlockers") or [],
        "seniorShortfallFix": {
            "status": senior.get("status"),
            "requiredSeniorReduction": senior.get("requiredSeniorReduction"),
            "requiredDsraOrEquityInjection": senior.get("requiredDsraOrEquityInjection"),
            "cashAvailable": senior.get("cashAvailable"),
            "shortfall": senior.get("shortfall"),
            "sourceLocators": senior.get("sourceLocators") or [],
        },
        "unsoldLtvFix": {
            "status": unsold.get("status"),
            "targetLtv": unsold.get("targetLtv"),
            "maxLtv": unsold.get("maxLtv"),
            "pfAmount": unsold.get("pfAmount"),
            "targetSupportedPfAmount": unsold.get("targetSupportedPfAmount"),
            "requiredPfReductionOrEquityIncrease": unsold.get("requiredPfReductionOrEquityIncrease"),
            "requiredAdditionalCollateralValue": unsold.get("requiredAdditionalCollateralValue"),
            "worstCell": unsold.get("worstCell") or {},
            "suggestedActions": unsold.get("suggestedActions") or [],
            "sourceLocators": unsold.get("sourceLocators") or [],
        },
        "recommendations": restructuring.get("recommendations") or [],
    }


def _review_plan(run: Dict[str, Any], risk_register: Dict[str, Any]) -> Dict[str, Any]:
    handoffs = run.get("handoffs") or []
    pending_handoffs = [
        {
            "handoffId": item.get("handoffId"),
            "fromAgent": item.get("fromAgent"),
            "toAgent": item.get("toAgent"),
            "status": item.get("status"),
            "reviewStatus": item.get("reviewStatus"),
            "blockingIssueCount": len(item.get("blockingIssues") or []),
            "blockingIssues": (item.get("blockingIssues") or [])[:10],
        }
        for item in handoffs
        if item.get("requiresHumanReview") and item.get("reviewStatus") not in {"approved", "waived", "resolved"}
    ]
    risks = risk_register.get("risks") or []
    pending_risks = [
        {
            "riskId": risk.get("riskId"),
            "code": risk.get("code"),
            "category": risk.get("category"),
            "severity": risk.get("severity"),
            "title": risk.get("title"),
            "reviewStatus": risk.get("reviewStatus"),
            "mitigant": risk.get("mitigant"),
            "sourceLocators": risk.get("sourceLocators") or [],
        }
        for risk in risks
        if risk.get("requiresHumanReview") and risk.get("reviewStatus") not in {"approved", "waived", "resolved"}
    ]
    return {
        "unresolvedHandoffReviewCount": len(pending_handoffs),
        "unresolvedRiskReviewCount": len(pending_risks),
        "pendingHandoffs": pending_handoffs,
        "pendingRisks": pending_risks,
        "reviewActions": [
            "Approve only when evidence and mitigants are attached.",
            "Use waived only when an authorized reviewer records a reason and substitute mitigant.",
            "Use needs_revision when the source agent must regenerate a patch/scenario/report section.",
            "Use resolved after evidence is attached and the relevant agent/risk row is rerun or revalidated.",
        ],
    }


def _rerun_plan() -> List[Dict[str, str]]:
    return [
        {
            "step": "1_land_evidence_attach",
            "command": "python services/api-server/scripts/apply_land_evidence_to_pilot.py",
            "expectedResult": "officialLandPriceCoverageRatio and registryEvidenceCoverageRatio increase toward 1.0.",
        },
        {
            "step": "2_pilot_rerun",
            "command": "python services/api-server/scripts/run_pilot_fixture.py --input services/api-server/data/runtime/pilot-input/pilot_input_payload.json --project-id pilot-after-evidence",
            "expectedResult": "Approval Readiness land blockers clear if evidence is complete.",
        },
        {
            "step": "3_financial_cure_attach",
            "command": "Attach PF reduction/equity/DSRA/collateral/forward-sale/bulk-sale/refinancing evidence and rerun scenarios.",
            "expectedResult": "UNSOLD_LTV_RESTRUCTURING_REQUIRED warning clears.",
        },
        {
            "step": "4_reviews",
            "command": "Use orchestration UI/API to approve, waive, resolve, or revise pending handoffs and material risks.",
            "expectedResult": "HANDOFF_REVIEWS_RESOLVED and RISK_REVIEWS_RESOLVED gates clear.",
        },
    ]


def render_markdown(package: Dict[str, Any]) -> str:
    summary = package["summary"]
    land = package["landEvidencePlan"]
    finance = package["financialCurePlan"]
    review = package["reviewPlan"]
    unsold = finance["unsoldLtvFix"]
    senior = finance["seniorShortfallFix"]

    lines = [
        "# Pilot Approval Blocker Resolution Package",
        "",
        f"Generated: {package['generatedAt']}",
        f"Run ID: {package.get('runId')}",
        f"Project ID: {package.get('projectId')}",
        "",
        "## Approval Status",
        "",
        f"- Approval ready: {summary['approvalReady']}",
        f"- Approval status: {summary.get('approvalStatus')}",
        f"- Credit decision: {summary.get('creditDecision')}",
        f"- Risk Register status: {summary.get('riskRegisterStatus')}",
        f"- Blocking issues: {summary.get('blockingIssueCount')}",
        f"- Warnings: {summary.get('warningCount')}",
        "",
        "## Blocker Actions",
        "",
        "| Code | Severity | Owner | Required action | Target |",
        "|---|---|---|---|---|",
    ]
    for blocker in package["blockers"]:
        lines.append(
            "| {code} | {severity} | {owner} | {action} | {target} |".format(
                code=blocker.get("code") or "",
                severity=blocker.get("severity") or "",
                owner=blocker.get("owner") or "",
                action=_md(blocker.get("requiredAction")),
                target=_md(blocker.get("targetArtifactOrScreen")),
            )
        )

    lines.extend(
        [
            "",
            "## Land Evidence Requirements",
            "",
            f"- Parcel count: {land.get('parcelCount')}",
            f"- Official land price missing: {land.get('officialLandPriceMissingCount')}",
            f"- Registry evidence missing: {land.get('registryEvidenceMissingCount')}",
            f"- Cadastral characteristics missing: {land.get('cadastralCharacteristicsMissingCount')}",
            f"- Total missing evidence fields: {land.get('totalMissingEvidenceCount')}",
            f"- Fill template: `{land.get('requiredTemplate')}`",
            f"- Apply script: `{land.get('attachmentScript')}`",
            "",
            "## Financial Cure Requirements",
            "",
            f"- Senior shortfall status: {senior.get('status')}",
            f"- Required senior reduction: {_money(senior.get('requiredSeniorReduction'))}",
            f"- Required DSRA/equity injection for senior: {_money(senior.get('requiredDsraOrEquityInjection'))}",
            f"- Unsold LTV status: {unsold.get('status')}",
            f"- Max unsold LTV: {unsold.get('maxLtv')}",
            f"- Required PF reduction or equity increase: {_money(unsold.get('requiredPfReductionOrEquityIncrease'))}",
            f"- Required additional collateral value: {_money(unsold.get('requiredAdditionalCollateralValue'))}",
            "",
            "## Review Workflow",
            "",
            f"- Pending handoff reviews: {review.get('unresolvedHandoffReviewCount')}",
            f"- Pending material risk reviews: {review.get('unresolvedRiskReviewCount')}",
            "",
            "## Rerun Plan",
            "",
        ]
    )
    for item in package["rerunPlan"]:
        lines.extend(
            [
                f"### {item['step']}",
                "",
                f"- Command/action: `{item['command']}`",
                f"- Expected result: {item['expectedResult']}",
                "",
            ]
        )
    lines.extend(["## Non-Fabrication Policy", ""])
    for rule in package["nonFabricationPolicy"]:
        lines.append(f"- {rule}")
    return "\n".join(lines)


def _money(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return f"KRW {value:,.0f}"


def _md(value: Any) -> str:
    return str(value or "").replace("|", "/")


if __name__ == "__main__":
    raise SystemExit(main())
