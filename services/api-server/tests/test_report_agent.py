import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agents.report_agent import ReportAgent
from db.repositories.project_snapshot_repository import project_snapshot_content_hash
from db.repositories.report_repository import ReportRepository
from main import app
from schemas.report import PHASE_11_SECTION_ORDER, PHASE_11_TEMPLATE
from use_cases import report_generation
from use_cases.report_generation import publish_saved_report_use_case


def load_example():
    path = Path(__file__).parents[1] / "data" / "examples" / "daegu_bondong_mixed_use_pf.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_hana_fixture():
    path = Path(__file__).parents[1] / "data" / "fixtures" / "hana_pf_review_anonymized.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_generate_structured_phase_11_report_has_all_sections():
    report = ReportAgent().generate_structured_report(load_example())

    assert report["template"] == PHASE_11_TEMPLATE
    assert report["qualityChecks"]["isComplete"] is True
    assert report["qualityChecks"]["actualSectionCount"] == 20
    assert [section["key"] for section in report["sectionOrder"]] == [
        key for key, _ in PHASE_11_SECTION_ORDER
    ]


def test_generate_structured_phase_11_report_calculates_financial_metrics():
    report = ReportAgent().generate_structured_report(load_example())
    sections = report["sections"]

    assert sections["financialTerms"]["totalAmount"] == 70_000_000_000
    assert sections["financialTerms"]["totalAmountMillion"] == 70_000
    assert sections["businessFeasibility"]["metrics"]["ltv"] == 70_000_000_000 / 150_000_000_000
    assert sections["businessFeasibility"]["metrics"]["developmentProfitMillion"] == 30_000


def test_captured_workbook_analysis_cannot_pass_final_report_quality():
    from agents.orchestration_agent import OrchestrationAgent
    data = load_example()
    data["cashFlowRunResult"] = {
        "sourceBasis": "captured_workbook_cash_flow", "analysisOnly": True,
        "reportEligible": False, "status": "completed",
        "summary": {"totalRevenue": 123, "totalProjectCost": 100,
                    "businessDeclaredTotals": {"total_revenue": 120, "total_cost": 99}},
        "monthlyCashFlow": [{"monthIndex": 0, "cashIn": 123, "cashOut": 100}],
    }
    report = ReportAgent().generate_structured_report(data)
    section = report["sections"]["summaryCashFlow"]
    assert section["summary"] == data["cashFlowRunResult"]["summary"]
    assert section["businessDeclaredTotals"]["total_revenue"] == 120
    assert section["reportEligible"] is False
    assert report["qualityChecks"]["cashFlowReportEligible"] is False
    assert report["qualityChecks"]["cashFlowUsageRestriction"] in section["assumption"]
    names = ["landAgentReview", "developerFeasibility", "feasibility", "pfStructure",
             "marketEvidenceReview", "marketabilityAnalysis", "dataQualityReview", "reconciliationGate",
             "legalPermitReview", "riskAssessment", "riskRegister", "creditReviewDecision",
             "restructuringPlan", "evidenceGraph"]
    run = {"artifacts": {**{name: {} for name in names}, "phase11Report": {"report": report}}}
    quality = OrchestrationAgent()._quality_check(run, {"status": "passed"},
                                                 {"status": "passed", "baseline_reviewed": True})
    assert quality["missingArtifacts"] == []
    assert quality["reportComplete"] is True
    assert quality["pass"] is False


def test_hana_style_anonymized_fixture_covers_phase_11_payload():
    fixture = load_hana_fixture()
    report = ReportAgent().generate_structured_report(fixture)
    sections = report["sections"]

    assert fixture["fixture_meta"]["anonymized"] is True
    assert report["qualityChecks"]["isComplete"] is True
    assert [section["key"] for section in report["sectionOrder"]] == [
        key for key, _title in PHASE_11_SECTION_ORDER
    ]
    assert sections["financialTerms"]["totalAmount"] == 100_000_000_000
    assert len(sections["financialTerms"]["tranches"]) == 3
    assert sections["discountTrigger"]["triggers"][1]["discountRate"] == 0.05
    assert len(sections["landSecuring"]["parcels"]) == 2
    assert sections["businessFeasibility"]["metrics"]["developmentProfit"] == 29_700_000_000
    assert sections["drawdownConditions"]["precedent"][2] == "Building permit approval completed"


def test_generate_report_endpoint_returns_markdown_and_structured_report():
    client = TestClient(app)
    projects = client.get("/projects/").json()
    project_id = next(
        item["project"]["id"]
        for item in projects
        if item.get("covenants", {}).get("discount_triggers")
    )

    res = client.post(
        f"/projects/{project_id}/reports/generate",
        json={"template": PHASE_11_TEMPLATE, "options": {"format": "json+markdown"}},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["template"] == PHASE_11_TEMPLATE
    assert body["metadata"]["complete"] is True
    assert body["metadata"]["section_count"] == 20
    assert "report_markdown" in body
    assert body["report"]["sections"]["discountTrigger"]["triggers"]
    assert body["version"] >= 1


def test_report_export_endpoints_create_history_and_download_artifacts():
    client = TestClient(app)
    projects = client.get("/projects/").json()
    project_id = next(
        item["project"]["id"]
        for item in projects
        if item.get("covenants", {}).get("discount_triggers")
    )
    report = client.post(
        f"/projects/{project_id}/reports/generate",
        json={"template": PHASE_11_TEMPLATE},
    ).json()
    report_id = report["report_id"]

    markdown_export = client.post(
        f"/projects/{project_id}/reports/{report_id}/exports",
        json={"format": "markdown"},
    )
    assert markdown_export.status_code == 200
    markdown_body = markdown_export.json()
    assert markdown_body["format"] == "markdown"
    assert markdown_body["file_name"].endswith(".md")
    assert markdown_body["byte_size"] > 0
    assert "content" not in markdown_body

    html_export = client.post(
        f"/projects/{project_id}/reports/{report_id}/exports",
        json={"format": "html"},
    ).json()
    assert html_export["format"] == "html"
    assert html_export["file_name"].endswith(".html")

    pdf_export = client.post(
        f"/projects/{project_id}/reports/{report_id}/exports",
        json={"format": "pdf"},
    ).json()
    assert pdf_export["format"] == "pdf"
    assert pdf_export["file_name"].endswith(".pdf")
    assert pdf_export["content_type"] == "application/pdf"
    assert pdf_export["byte_size"] > 0
    assert "content" not in pdf_export

    docx_export = client.post(
        f"/projects/{project_id}/reports/{report_id}/exports",
        json={"format": "docx"},
    ).json()
    assert docx_export["format"] == "docx"
    assert docx_export["file_name"].endswith(".docx")
    assert docx_export["content_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert docx_export["byte_size"] > 0
    assert "content" not in docx_export

    exports = client.get(f"/projects/{project_id}/reports/{report_id}/exports").json()
    assert len(exports) >= 4

    detail = client.get(f"/projects/{project_id}/reports/{report_id}").json()
    assert detail["export_count"] >= 4
    assert detail["latest_exported_at"]

    download = client.get(
        f"/projects/{project_id}/reports/{report_id}/exports/{html_export['export_id']}/download"
    )
    assert download.status_code == 200
    assert "text/html" in download.headers["content-type"]
    assert "<!doctype html>" in download.text

    pdf_download = client.get(
        f"/projects/{project_id}/reports/{report_id}/exports/{pdf_export['export_id']}/download"
    )
    assert pdf_download.status_code == 200
    assert pdf_download.headers["content-type"] == "application/pdf"
    assert pdf_download.content.startswith(b"%PDF-1.4")

    docx_download = client.get(
        f"/projects/{project_id}/reports/{report_id}/exports/{docx_export['export_id']}/download"
    )
    assert docx_download.status_code == 200
    assert docx_download.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert docx_download.content.startswith(b"PK")


def test_report_export_rejects_unsupported_format():
    client = TestClient(app)
    projects = client.get("/projects/").json()
    project_id = projects[0]["project"]["id"]
    report = client.post(
        f"/projects/{project_id}/reports/generate",
        json={"template": PHASE_11_TEMPLATE},
    ).json()

    res = client.post(
        f"/projects/{project_id}/reports/{report['report_id']}/exports",
        json={"format": "xlsx"},
    )
    assert res.status_code == 400


def test_manual_report_remains_draft_and_cannot_be_finally_published():
    client = TestClient(app)
    project_id = client.get("/projects/").json()[0]["project"]["id"]
    report = client.post(
        f"/projects/{project_id}/reports/generate",
        json={"template": PHASE_11_TEMPLATE},
    ).json()

    detail = client.get(
        f"/projects/{project_id}/reports/{report['report_id']}"
    ).json()
    assert detail["status"] == "draft"
    assert detail["metadata"]["report_eligible"] is False
    assert detail["metadata"]["usage_restriction"] == (
        "analysis_only_not_final_approval"
    )

    publication = client.post(
        f"/projects/{project_id}/reports/{report['report_id']}/publish"
    )
    assert publication.status_code == 409
    assert "orchestration-bound" in publication.json()["detail"]


def test_orchestration_bound_report_publication_is_hashed_and_idempotent(monkeypatch):
    store = {}
    repository = ReportRepository(store, lambda: None)
    structured = ReportAgent().generate_structured_report(load_example())
    markdown = ReportAgent().generate_report(load_example())
    record = repository.create(
        "project-1",
        structured,
        markdown,
        run_id="run-1",
    )
    run = {
        "run_id": "run-1",
        "project_id": "project-1",
        "workflow": "pf-review",
        "approvalStatus": "approved",
        "approval": {
            "approved_by": "checker-1",
            "approved_at": "2026-07-24T00:00:00Z",
        },
        "artifacts": {
            "projectSnapshot": {"snapshotId": "snapshot-run-1"},
        },
    }
    snapshot = {
        "schemaVersion": "realytics-project-snapshot-v1",
        "snapshotId": "snapshot-run-1",
        "projectId": "project-1",
        "runId": "run-1",
    }
    snapshot["snapshotHash"] = project_snapshot_content_hash(snapshot)
    run["artifacts"]["projectSnapshot"]["snapshotHash"] = snapshot["snapshotHash"]

    class Orchestrations:
        def get(self, project_id, run_id):
            assert (project_id, run_id) == ("project-1", "run-1")
            return run

    class Snapshots:
        def get(self, project_id, snapshot_id):
            assert (project_id, snapshot_id) == (
                "project-1",
                "snapshot-run-1",
            )
            return snapshot

    monkeypatch.setattr(
        report_generation,
        "refresh_approval_readiness_gate",
        lambda _run: {
            "schemaVersion": "realytics-approval-readiness-gate-v1",
            "gateType": "approval_readiness",
            "status": "ready_for_approval",
            "pass": True,
            "summary": {"blockingIssueCount": 0, "warningCount": 1},
        },
    )

    published = publish_saved_report_use_case(
        repository,
        Orchestrations(),
        Snapshots(),
        "project-1",
        record["report_id"],
        published_by="publisher-1",
        published_at="2026-07-24T01:00:00Z",
    )
    assert published["status"] == "published"
    assert published["metadata"]["report_eligible"] is True
    assert published["metadata"]["content_sha256"].startswith("sha256:")
    assert published["publication"]["decisionPackage"]["projectSnapshotId"] == (
        "snapshot-run-1"
    )
    assert published["publication"]["decisionPackage"]["projectSnapshotHash"] == (
        snapshot["snapshotHash"]
    )

    repeated = publish_saved_report_use_case(
        repository,
        Orchestrations(),
        Snapshots(),
        "project-1",
        record["report_id"],
        published_by="publisher-1",
        published_at="2026-07-24T01:00:00Z",
    )
    assert repeated is published

    snapshot["schemaVersion"] = "tampered"
    with pytest.raises(ValueError, match="hash seal is invalid"):
        publish_saved_report_use_case(
            repository,
            Orchestrations(),
            Snapshots(),
            "project-1",
            record["report_id"],
            published_by="publisher-1",
            published_at="2026-07-24T01:00:00Z",
        )

    snapshot["schemaVersion"] = "realytics-project-snapshot-v1"
    run["artifacts"]["projectSnapshot"]["snapshotHash"] = "sha256:" + ("0" * 64)
    with pytest.raises(ValueError, match="hash lineage does not match"):
        publish_saved_report_use_case(
            repository,
            Orchestrations(),
            Snapshots(),
            "project-1",
            record["report_id"],
            published_by="publisher-1",
            published_at="2026-07-24T01:00:00Z",
        )


def test_report_land_acquisition_tolerates_parsed_ledger_parcels():
    """실제 토지조서 파싱 필지(share/owner/area_sqm 없이 camelCase areaSqm만)로도
    보고서 생성이 크래시하지 않아야 한다 (통합 리허설에서 발견한 500 회귀 방지)."""
    agent = ReportAgent()
    data = {
        "land_acquisition": [
            {"addressFragment": "본리 1-3", "dong": "본리동", "lotNumber": "1-3", "areaSqm": 735.5, "landCategory": "대"},
            {"addressFragment": "본리 1-4", "lotNumber": "1-4"},  # area/share 전부 없음
        ]
    }
    section = agent.generate_report(data)
    assert "본리 1-3" in section
    assert "736" in section
    assert "735.5" not in section
    structured = agent.generate_structured_report(data)
    assert structured["sections"]["landSecuring"]["parcels"][0]["areaSqm"] == 735.5
    # share/area 없는 필지도 '-'로 안전 렌더
    assert "본리 1-4" in section


def test_report_use_of_funds_tolerates_missing_amount():
    agent = ReportAgent()
    section = agent.generate_report({"use_of_funds": [{"item": "토지비"}]})
    assert "토지비" in section
