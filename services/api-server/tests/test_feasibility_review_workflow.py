from io import BytesIO

import openpyxl
from fastapi.testclient import TestClient

from main import app
from routers import projects
from storage import get_project_record


def test_manual_source_correction_assumption_save_reopen_and_calculate(monkeypatch):
    # conftest isolates both upload and viewer repositories in the same temp runtime.
    monkeypatch.setattr(projects, "extract_feasibility_fields_via_llm", lambda *args: (_ for _ in ()).throw(AssertionError("No AI calls")))
    client = TestClient(app)
    pid = client.post("/projects/", json={"project": {"name": "R1 review"}, "feasibility_input": {"total_revenue": 100, "total_cost": 80}}).json()["id"]
    try:
        state = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "손익"
        ws["A1"], ws["B1"] = "총매출(원)", 853951767703
        stream = BytesIO()
        wb.save(stream)
        original = stream.getvalue()
        uploaded = client.post(f"/projects/{pid}/feasibility-workbook/extract?manual_review=true", files={"file": ("review.xlsx", original, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["parsingSource"] == "manual_review"
        source = uploaded.json()["upload"]
        preview = client.get(f"/projects/{pid}/source-documents/{source['archiveId']}/workbook/cells", params={"sheet_name": "손익", "cell_reference": "B1"})
        assert preview.status_code == 200, preview.text
        assert any(cell["displayValue"] == 853951767703 for row in preview.json()["rows"] for cell in row["cells"])
        payload = {
            "expectedReviewHash": state["reviewHash"],
            "feasibility_input": {"total_revenue": 853951767703, "finance_cost": 123456789},
            "fieldReviews": {
                "total_revenue": {"origin": "document_correction", "sourceCell": "손익!B1", "reason": "원본 값으로 수정"},
                "finance_cost": {"origin": "user_assumption", "reason": "누락 비용을 분석용 가정으로 입력"},
            },
            "fieldSourceDocuments": {"total_revenue": {"archiveId": source["archiveId"], "originalFileName": "spoof.xlsx"}},
        }
        saved = client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json=payload)
        assert saved.status_code == 200, saved.text
        reopened = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
        assert reopened["values"]["total_revenue"] == 853951767703
        assert reopened["values"]["finance_cost"] == 123456789
        assert reopened["fieldReviews"]["total_revenue"]["sourceDocument"]["originalFileName"] == "review.xlsx"
        assert reopened["fieldReviews"]["finance_cost"]["origin"] == "user_assumption"
        assert reopened["fieldReviews"]["finance_cost"]["approvalStatus"] == "not_requested"
        assert get_project_record(pid)["feasibility_input"]["finance_cost"] == 123456789
        calculated = client.post(f"/projects/{pid}/feasibility/calculate", json={})
        assert calculated.status_code == 200, calculated.text
        assert calculated.json()["total_revenue"] == 853951767703
        stale = client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json=payload)
        assert stale.status_code == 409
        # No correction may overwrite the archived file.
        content = client.get(f"/projects/{pid}/source-documents/{source['archiveId']}/content")
        assert content.status_code == 200
        assert content.content == original
        payload["expectedReviewHash"] = reopened["reviewHash"]
        payload["fieldSourceDocuments"]["total_revenue"]["archiveId"] = "a" * 64
        rejected = client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json=payload)
        assert rejected.status_code == 400
        assert client.get(f"/projects/{pid}/feasibility-workbook/review").json() == reopened
    finally:
        client.delete(f"/projects/{pid}")
