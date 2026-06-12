from __future__ import annotations

import json
from pathlib import Path

from scripts.prepare_pilot_workspace import prepare_workspace
from scripts.run_pilot_fixture import summarize_run
from scripts.build_land_evidence_request import build_manual_template, build_request
from scripts.apply_land_evidence_to_pilot import apply_evidence
from scripts.build_pilot_blocker_resolution_package import build_resolution_package
from scripts.apply_pilot_data_quality_override import (
    _build_override,
    _find_check,
    _find_source_value,
    _upsert_override,
)
from scripts.apply_pilot_financial_cure import _apply_cure
from scripts.apply_pilot_review_actions import apply_review_actions, build_review_action_template
from agents.orchestration_agent import OrchestrationAgent
from agents.land_ledger_agent import LandLedgerAgent


def test_prepare_pilot_workspace_creates_payload_and_manifest(tmp_path):
    sample_dir = tmp_path / "sample"
    sample_dir.mkdir()
    source_file = sample_dir / "01.land-ledger.xls"
    source_file.write_bytes(b"sample-land-ledger")

    workspace = tmp_path / "pilot"
    template = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pilot_project_input_package_template.json"

    summary = prepare_workspace(
        workspace=workspace,
        sample_dir=sample_dir,
        template=template,
        copy_samples=False,
        overwrite=False,
    )

    assert summary["documentCount"] == 1
    assert (workspace / "pilot_input_payload.json").exists()
    assert (workspace / "README.md").exists()
    manifest = json.loads((workspace / "source_document_manifest.json").read_text(encoding="utf-8"))
    assert manifest["documentCount"] == 1
    assert manifest["documents"][0]["status"] == "referenced_external_file"
    assert manifest["documents"][0]["sha256"]


def test_pilot_run_summary_contains_key_review_sections():
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pilot_project_input_package_template.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    run = OrchestrationAgent().run_pf_review("pilot-summary-test", payload)

    summary = summarize_run(run)

    assert summary["status"] == "completed"
    assert summary["stepCount"] == 18
    assert summary["qualityCheck"]["pass"] is True
    assert "approvalReadinessGate" in summary
    assert "creditReview" in summary
    assert "riskRegister" in summary
    assert "land" in summary
    assert "restructuringPlan" in summary
    assert "evidenceGraph" in summary


def test_pilot_blocker_resolution_package_summarizes_approval_blockers():
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pilot_project_input_package_template.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    run = OrchestrationAgent().run_pf_review("pilot-blocker-package-test", payload)
    land_request = build_request(payload)

    package = build_resolution_package(run, land_request)

    assert package["schemaVersion"] == "realytics-pilot-blocker-resolution-package-v1"
    assert package["summary"]["approvalReady"] is False
    assert package["summary"]["blockingIssueCount"] >= 1
    assert package["landEvidencePlan"]["parcelCount"] >= 0
    assert package["landEvidencePlan"]["totalMissingEvidenceCount"] >= 0
    assert package["reviewPlan"]["unresolvedRiskReviewCount"] >= 1
    assert isinstance(package["reviewPlan"]["pendingHandoffs"], list)
    assert isinstance(package["reviewPlan"]["pendingRisks"], list)
    assert "rerunPlan" in package
    assert package["nonFabricationPolicy"]


def test_pilot_data_quality_override_clears_unit_count_blocker():
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pilot_project_input_package_template.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    baseline_run = OrchestrationAgent().run_pf_review("pilot-dq-unit-baseline-test", payload)
    check = _find_check(baseline_run, "unit_count_consistency")
    source_value = _find_source_value(check, "product_mix.units.sum")
    override = _build_override(
        check=check,
        check_key="unit_count_consistency",
        canonical_field="architectural_plan.units",
        approved_value=260,
        approved_source="product_mix.units.sum",
        source_value=source_value,
        approved_by="credit_officer",
        reason="Product mix schedule is authoritative for this pilot test.",
    )
    patched_payload = _upsert_override(payload, override)

    rerun = OrchestrationAgent().run_pf_review("pilot-dq-unit-cleared-test", patched_payload)
    data_quality = rerun["artifacts"]["dataQualityReview"]
    unit_check = next(check for check in data_quality["checks"] if check["key"] == "unit_count_consistency")
    blockers = [item["code"] for item in rerun["artifacts"]["approvalReadinessGate"]["blockers"]]

    assert unit_check["status"] == "overridden"
    assert data_quality["summary"]["blockingCount"] == 0
    assert data_quality["appliedOverrides"][0]["overrideId"] == "ovr-pilot-unit-count-consistency-260"
    assert "DATA_QUALITY_NOT_BLOCKING" not in blockers


def test_pilot_additional_collateral_cure_clears_unsold_ltv_warning_after_dq_override():
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pilot_project_input_package_template.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    baseline_run = OrchestrationAgent().run_pf_review("pilot-financial-cure-baseline-test", payload)
    check = _find_check(baseline_run, "unit_count_consistency")
    source_value = _find_source_value(check, "product_mix.units.sum")
    override = _build_override(
        check=check,
        check_key="unit_count_consistency",
        canonical_field="architectural_plan.units",
        approved_value=260,
        approved_source="product_mix.units.sum",
        source_value=source_value,
        approved_by="credit_officer",
        reason="Product mix schedule is authoritative for this pilot test.",
    )
    dq_payload = _upsert_override(payload, override)
    dq_run = OrchestrationAgent().run_pf_review("pilot-financial-cure-dq-test", dq_payload)
    amount = dq_run["artifacts"]["restructuringPlan"]["unsoldLtvFix"]["requiredAdditionalCollateralValue"]

    cured_payload = _apply_cure(
        dq_payload,
        prior_run=dq_run,
        cure_type="additional_collateral",
        amount=amount,
        evidence_id="collateral-evidence-test-001",
        reviewed_by="credit_officer",
    )
    cured_run = OrchestrationAgent().run_pf_review("pilot-financial-cure-cleared-test", cured_payload)
    scenario_risk_codes = [flag["code"] for flag in cured_run["artifacts"]["scenariosEvaluation"]["riskFlags"]]
    approval_warnings = [item["code"] for item in cured_run["artifacts"]["approvalReadinessGate"]["warnings"]]

    assert cured_payload["scenarioDrivers"]["additionalCollateralValue"] == amount
    assert cured_run["artifacts"]["restructuringPlan"]["unsoldLtvFix"]["status"] == "clear"
    assert "UNSOLD_LTV_BREACH" not in scenario_risk_codes
    assert "UNSOLD_LTV_RESTRUCTURING_REQUIRED" not in approval_warnings


def test_pilot_review_action_template_lists_remaining_handoffs_and_risks():
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pilot_project_input_package_template.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    run = OrchestrationAgent().run_pf_review("pilot-review-template-test", payload)

    template = build_review_action_template(run)

    assert template["schemaVersion"] == "realytics-pilot-review-actions-v1"
    assert template["reviewPolicy"]["nonFabrication"] is True
    assert len(template["handoffReviews"]) >= 1
    assert len(template["riskReviews"]) >= 1
    risk_codes = {item["code"] for item in template["riskReviews"]}
    assert "PERMIT_SCHEDULE_READINESS" in risk_codes


def test_pilot_review_actions_require_evidence_for_risk_resolution():
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pilot_project_input_package_template.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    run = OrchestrationAgent().run_pf_review("pilot-review-evidence-required-test", payload)
    template = build_review_action_template(run)
    first_risk = template["riskReviews"][0]

    try:
        apply_review_actions(run, {
            "riskReviews": [
                {
                    "riskId": first_risk["riskId"],
                    "reviewStatus": "resolved",
                    "reviewedBy": "legal_reviewer",
                    "comment": "Resolved by reviewer.",
                }
            ]
        })
    except ValueError as exc:
        assert "requires evidenceIds or sourceLocators" in str(exc)
    else:
        raise AssertionError("Risk resolution without evidence should fail")


def test_pilot_review_actions_apply_source_backed_resolutions():
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pilot_project_input_package_template.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    run = OrchestrationAgent().run_pf_review("pilot-review-actions-test", payload)
    template = build_review_action_template(run)
    actions = {
        "reviewedAt": "2026-06-12T00:00:00+00:00",
        "handoffReviews": [
            {
                "handoffId": handoff["handoffId"],
                "reviewStatus": "approved",
                "reviewedBy": "credit_officer",
                "comment": "Source-backed handoff accepted for pilot review action test.",
                "evidenceIds": [f"handoff-evidence-{index}"],
                "sourceLocators": [
                    {
                        "sourceType": "pilot_review_action",
                        "sourceUrlOrEvidenceId": f"handoff-evidence-{index}",
                        "locatorPrecision": "review_action",
                    }
                ],
            }
            for index, handoff in enumerate(template["handoffReviews"], start=1)
        ],
        "riskReviews": [
            {
                "riskId": risk["riskId"],
                "reviewStatus": "resolved",
                "reviewedBy": "legal_reviewer",
                "comment": "Source-backed evidence attached for pilot review action test.",
                "evidenceIds": [f"risk-evidence-{index}"],
                "sourceLocators": [
                    {
                        "sourceType": "pilot_review_action",
                        "sourceUrlOrEvidenceId": f"risk-evidence-{index}",
                        "locatorPrecision": "review_action",
                    }
                ],
            }
            for index, risk in enumerate(template["riskReviews"], start=1)
        ],
    }

    reviewed = apply_review_actions(run, actions)
    gate = reviewed["artifacts"]["approvalReadinessGate"]

    assert gate["summary"]["unresolvedHandoffReviewCount"] == 0
    assert gate["summary"]["unresolvedRiskReviewCount"] == 0
    assert "HANDOFF_REVIEWS_RESOLVED" not in [item["code"] for item in gate["blockers"]]
    assert "RISK_REVIEWS_RESOLVED" not in [item["code"] for item in gate["blockers"]]


def test_land_evidence_template_and_attachment_workflow():
    rows = [
        ["sample"],
        [""],
        ["순번", "토지소재", "지번", "지목", "공부상면적", "", "소유자", "계약금액", "", "계약금", "", "중도금", "잔금"],
        ["", "동", "지번", "", "㎡", "평", "", "개별매입금액", "합계금액", "기지급액", "미지급액"],
        [1, "본리", "1-4", "대", 100.0, 30.25, "Seller A", 100000000.0, 100000000.0, 10000000.0, 0, 0, 90000000.0],
        ["합  계", "", "", "", 100.0, 30.25, "", "", 100000000.0, 10000000.0, 0, 0, 90000000.0],
    ]
    analysis = LandLedgerAgent().analyze_rows(rows, sheet_name="ledger", source_file="ledger.xls")
    analysis = LandLedgerAgent().enrich_analysis(analysis, {"legalDongCode": "2729011700"})
    payload = {
        "project": {"name": "Evidence workflow test"},
        "landLedger": analysis,
    }

    request = build_request(payload)
    template = build_manual_template(request)
    pnu = request["parcelEvidenceRequests"][0]["pnu"]
    template["officialLandPrices"][0].update({
        "pricePerSqm": 1000000,
        "baseYear": 2026,
        "baseDate": "2026-01-01",
        "sourceName": "manual_official_price",
        "sourceUrlOrEvidenceId": "official-price-evidence-001",
        "confidence": 0.95,
    })
    template["cadastralCharacteristics"][0].update({
        "standardLandCategoryCode": "08",
        "zoning": "commercial",
        "sourceName": "manual_cadastral",
        "sourceUrlOrEvidenceId": "cadastral-evidence-001",
        "baseDate": "2026-01-01",
        "confidence": 0.9,
    })
    template["registryEvidence"][0].update({
        "evidenceId": "registry-evidence-001",
        "sourceName": "manual_registry",
        "sourceUrlOrEvidenceId": "registry-evidence-001",
        "issuedAt": "2026-05-28",
        "registeredOwner": "Seller A",
        "ownerMatchStatus": "match",
        "ownershipShare": 1.0,
        "mortgageExists": False,
        "seizureExists": False,
        "provisionalRegistrationExists": False,
        "easementExists": False,
        "trustRegistrationExists": False,
        "releaseRequired": False,
        "verificationStatus": "attached_pending_legal_review",
        "confidence": 0.9,
    })

    updated, summary = apply_evidence(payload, template, evidence_file="filled-template.json")

    assert template["officialLandPrices"][0]["pnu"] == pnu
    assert summary["officialLandPricesApplied"] == 1
    assert summary["registryEvidenceApplied"] == 1
    assert summary["officialLandPriceCoverageRatio"] == 1
    assert summary["registryEvidenceCoverageRatio"] == 1
    assert updated["landEvidence"]["officialLandPriceRecordCount"] == 1
