from __future__ import annotations

from fastapi.testclient import TestClient

from main import app
from agents.document_classifier_agent import classify_document


def test_classifies_business_plan_pdf_by_name():
    r = classify_document("본리동 사업계획서 IR.pdf", content_type="application/pdf")
    assert r["documentType"] == "business_plan"
    assert r["label"] == "사업계획서 / IR"
    assert r["confidence"] > 0.55 and r["needsReview"] is False
    assert any("사업계획" in reason for reason in r["reasons"])


def test_classifies_land_ledger_xls_by_name():
    r = classify_document("01.본리동토지조서.xls")
    assert r["documentType"] == "land_ledger"
    assert r["suggestedAction"] == "land_ledger_import"


def test_feasibility_workbook_by_sheet_names_beats_extension():
    r = classify_document(
        "검토자료.xlsx",
        sheet_names=["입력", "분양", "사업수지", "2.현금흐름", "공정율"],
    )
    assert r["documentType"] == "feasibility_workbook"
    assert r["confidence"] > 0.7  # strong sheet signal


def test_land_evidence_json_by_keys():
    r = classify_document("evidence.json", json_keys=["records", "officialLandPrice", "registryEvidence"])
    assert r["documentType"] == "land_evidence"


def test_unknown_when_no_signal():
    r = classify_document("scan001.png", content_type="image/png")
    assert r["documentType"] == "unknown"
    assert r["confidence"] == 0.0 and r["needsReview"] is True
    assert r["suggestedAction"] == "manual_review"


def test_extension_prior_routes_bare_pdf():
    r = classify_document("document.pdf")
    assert r["documentType"] == "business_plan"  # ext prior, low confidence
    assert r["needsReview"] is True


def test_smart_intake_endpoint_classifies_and_archives_multiple_files():
    client = TestClient(app)
    project_id = client.get("/projects/").json()[0]["project"]["id"]
    files = [
        ("files", ("본리동 사업계획서.pdf", b"%PDF-1.4 dummy", "application/pdf")),
        ("files", ("01.토지조서.xls", b"\xd0\xcf dummy xls", "application/vnd.ms-excel")),
        ("files", ("evidence.json", b'{"records": [], "officialLandPrice": 0}', "application/json")),
    ]
    res = client.post(f"/projects/{project_id}/intake/classify", files=files)
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 3
    types = [f["documentType"] for f in body["files"]]
    assert types[0] == "business_plan"
    assert types[1] == "land_ledger"
    assert types[2] == "land_evidence"
    assert all(f.get("archiveStored") for f in body["files"])


def test_smart_intake_404_for_unknown_project():
    client = TestClient(app)
    res = client.post("/projects/__nope__/intake/classify", files=[("files", ("x.pdf", b"x", "application/pdf"))])
    assert res.status_code == 404


def test_candidates_ranked_and_capped():
    r = classify_document("본리동 사업수지 PF검토.xlsx", sheet_names=["사업수지", "현금흐름"])
    assert r["documentType"] == "feasibility_workbook"
    assert len(r["candidates"]) <= 4
    assert r["candidates"][0]["documentType"] == "feasibility_workbook"


def test_classifier_exposes_confidence_basis():
    r = classify_document("business_plan.pdf", content_type="application/pdf")
    assert "confidenceBasis" in r
    assert "자동분류 신뢰도" in r["confidenceBasis"]
