from io import BytesIO

import openpyxl
from fastapi.testclient import TestClient

from main import app
from routers import projects
from storage import get_project_record, list_calculation_run_records


def exercise_review_calculation_comparison(client, *, original, filename, values, cells, reasons, revenue_change):
    """Exercise one complete user action against real routes and durable storage."""
    from core.reviewed_monthly_financing import MONTHLY_FINANCING_POLICY
    from use_cases.cash_flow_comparison import content_hash

    pid = client.post("/projects/", json={"project": {"name": "격리 자료 검증", "type": "mixed_use"}}).json()["id"]
    state = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
    upload = client.post(f"/projects/{pid}/feasibility-workbook/extract?manual_review=true",
        files={"file": (filename, original, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert upload.status_code == 200, upload.text
    source = upload.json()["upload"]
    assumption = {"origin": "user_assumption", "reason": "흐름 검증용 가정: 24개월, 선순위 5%·수수료 0·분양대금 만기상환. 실제 약정 증거 아님."}
    body = {"expectedReviewHash": state["reviewHash"], "feasibility_input": values,
        "fieldReviews": {key: {"origin": "document_correction", "sourceCell": cells[key], "reason": reasons[key]} for key in values},
        "fieldSourceDocuments": {key: {"archiveId": source["archiveId"]} for key in values},
        "terms": {"schedule": {"construction_start": "2026-01-01", "completion": "2028-01-01"},
                  "financing": [{"tranche_id": "analysis-senior", "name": "분석용 대출 가정", "priority": "senior",
                                 "amount": values["total_cost"] // 2, "interest_rate": 0.05, "fee_rate": 0,
                                 "term_months": 24, "repayment_source": "분양대금"}]},
        "termReviews": {"schedule": assumption, "financing": assumption}}
    saved = client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json=body)
    assert saved.status_code == 200, saved.text

    def calculate():
        review = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
        response = client.post(f"/projects/{pid}/cash-flow/run", json={"reviewedMonthlyFinancing": {
            "policy": MONTHLY_FINANCING_POLICY, "reason": "실제 자료와 명시적 분석 가정의 흐름 검증; 최종 승인 자료 미확인",
            "expectedReviewHash": review["reviewHash"]}})
        assert response.status_code == 200, response.text
        assert response.json()["analysisOnly"] is True and response.json()["reportEligible"] is False
        row = next(row for row in list_calculation_run_records(pid, engine_key="cash_flow_engine") if row["calculationRunId"] == response.json()["calculationRunId"])
        assert row["resultSnapshot"]["summary"] == response.json()["summary"]
        assert row["resultSnapshot"]["sourceMetadata"] == row["inputSnapshot"]["sourceMetadata"]
        assert row["inputSnapshot"]["sourceMetadata"]["inputReview"]["termReviews"]["financing"]["origin"] == "user_assumption"
        return row

    baseline = calculate()
    state = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
    change_reason = "분석용 매출 증가 가정; 원본 매출이나 확인된 증거를 수정하지 않음"
    changed_input = client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json={
        "expectedReviewHash": state["reviewHash"], "feasibility_input": {"total_revenue": values["total_revenue"] + revenue_change},
        "fieldReviews": {"total_revenue": {"origin": "user_assumption", "reason": change_reason}}})
    assert changed_input.status_code == 200, changed_input.text
    changed = calculate()
    assert changed["calculationRunId"] != baseline["calculationRunId"]
    assert changed["resultSnapshot"]["summary"]["totalRevenue"] - baseline["resultSnapshot"]["summary"]["totalRevenue"] == revenue_change
    state = client.get(f"/projects/{pid}/cash-flow/comparison").json()
    selected = client.post(f"/projects/{pid}/cash-flow/comparison", json={"baselineRunId": baseline["calculationRunId"],
        "comparisonRunId": changed["calculationRunId"], "expectedComparisonHash": state["comparisonHash"]})
    assert selected.status_code == 200, selected.text
    state = client.get(f"/projects/{pid}/cash-flow/comparison").json()
    assert state["selection"]["saved"] is True
    assert any(row["classification"].startswith("원본 대조") for row in state["comparison"]["baseline"]["inputReviews"])
    assert any(row["reason"] == change_reason for row in state["comparison"]["comparison"]["inputReviews"])
    draft = client.post(f"/projects/{pid}/reports/cash-flow-comparison", json={"expectedComparisonHash": state["comparisonHash"]})
    assert draft.status_code == 200, draft.text
    draft = draft.json()
    snapshot = draft["report"]["cashFlowComparisonSnapshot"]
    assert snapshot["comparison"] == state["comparison"]
    assert snapshot["baselineRun"] == baseline and snapshot["comparisonRun"] == changed
    assert draft["report"]["cashFlowComparisonSnapshotHash"] == content_hash(snapshot)
    assert change_reason in draft["report_markdown"] and cells["total_revenue"] in draft["report_markdown"]
    # Later working input edits cannot change the stored screen or exported file.
    state = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
    later = client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json={
        "expectedReviewHash": state["reviewHash"], "feasibility_input": {"total_revenue": values["total_revenue"] + revenue_change * 2},
        "fieldReviews": {"total_revenue": {"origin": "user_assumption", "reason": "후속 미계산 가정"}}})
    assert later.status_code == 200, later.text
    reopened = client.get(f"/projects/{pid}/reports/{draft['report_id']}")
    assert reopened.status_code == 200 and reopened.json()["report"] == draft["report"]
    outputs = {}
    for fmt in ("json", "pdf", "docx"):
        exported = client.post(f"/projects/{pid}/reports/{draft['report_id']}/exports", json={"format": fmt})
        assert exported.status_code == 200, exported.text
        download = client.get(f"/projects/{pid}/reports/{draft['report_id']}/exports/{exported.json()['export_id']}/download")
        assert download.status_code == 200
        if fmt == "json":
            assert download.json()["report"] == draft["report"]
        outputs[fmt] = download.content
    assert client.get(f"/projects/{pid}/source-documents/{source['archiveId']}/content").content == original
    assert not get_project_record(pid).get("canonicalFeasibilityStudyV2")
    return {"baseline": baseline, "changed": changed, "draft": draft, "outputs": outputs}


def test_input_review_recalculation_comparison_and_exports_keep_one_frozen_basis():
    workbook = openpyxl.Workbook()
    workbook.active.title = "사업수지"
    values = {"total_revenue": 12000, "total_cost": 10000, "land_cost": 3000,
              "construction_cost": 6000, "finance_cost": 0, "other_cost": 1000}
    cells = {key: f"사업수지!B{index}" for index, key in enumerate(values, 1)}
    for index, value in enumerate(values.values(), 1):
        workbook.active.cell(index, 2, value)
    stream = BytesIO()
    workbook.save(stream)
    exercise_review_calculation_comparison(TestClient(app), original=stream.getvalue(), filename="synthetic-review.xlsx",
        values=values, cells=cells, reasons={key: "합성 원본 대조" for key in values}, revenue_change=1000)


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
        run = next(row for row in list_calculation_run_records(pid, engine_key="feasibility")
                   if row["calculationRunId"] == calculated.json()["calculationRunId"])
        assert run["inputSnapshot"]["total_revenue"] == reopened["values"]["total_revenue"]
        assert run["inputSnapshot"]["finance_cost"] == reopened["values"]["finance_cost"]
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
