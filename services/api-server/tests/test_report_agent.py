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
