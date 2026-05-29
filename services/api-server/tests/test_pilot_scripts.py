from __future__ import annotations

import json
from pathlib import Path

from scripts.prepare_pilot_workspace import prepare_workspace
from scripts.run_pilot_fixture import summarize_run
from scripts.build_land_evidence_request import build_manual_template, build_request
from scripts.apply_land_evidence_to_pilot import apply_evidence
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
