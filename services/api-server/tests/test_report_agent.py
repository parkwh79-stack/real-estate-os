import json
from pathlib import Path

from fastapi.testclient import TestClient

from agents.report_agent import ReportAgent
from main import app
from schemas.report import PHASE_11_SECTION_ORDER, PHASE_11_TEMPLATE


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
