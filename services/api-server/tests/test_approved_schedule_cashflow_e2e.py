from fastapi.testclient import TestClient

from main import app
from routers import projects


def test_approved_feasibility_schedule_reaches_calculation_run_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approved-schedule.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    client = TestClient(app)

    created = client.post("/projects/", json={"project": {"name": "Approved Schedule E2E"}})
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": project_id,
        "project_name": "Approved Schedule E2E",
        "asset_type": "apartment",
        "schedule": [
            {"key": "construction_start", "label": "construction_start", "planned_date": "2026-01"},
            {"key": "completion", "label": "completion", "planned_date": "2026-04"},
        ],
        "revenue_items": [{"key": "sales", "label": "Sales", "category": "sales", "amount": 1000}],
        "cost_items": [{"key": "cost", "label": "Cost", "category": "construction", "amount": 600}],
        "metrics": {"total_revenue": 1000, "total_cost": 600},
        "raw_import": {
            "productMonthlySalesRateScenarios": [{
                "products": [{
                    "productType": "apartment",
                    "productRevenue": 1000,
                    "collectionSteps": [
                        {"stage": "contract", "dueMonth": 0, "collectionRate": 0.1, "monthlyCollectionAmounts": [100, 0, 0, 0]},
                        {"stage": "interim", "dueMonth": 2, "collectionRate": 0.6, "monthlyCollectionAmounts": [0, 0, 600, 0]},
                        {"stage": "balance", "dueMonth": 3, "collectionRate": 0.3, "monthlyCollectionAmounts": [0, 0, 0, 300]},
                    ],
                    "provenance": {"sourceType": "excel_range", "sheetName": "02B_collection_schedule", "cell": "A4:AY6"},
                }]
            }]
        },
    }

    approved = client.post(
        f"/projects/{project_id}/feasibility-workbook/approve",
        json={
            "importId": "approved-schedule-import",
            "approvedBy": "e2e-reviewer",
            "comment": "Approved product collection schedule for Cash Flow.",
            "feasibilityStudyV2Draft": draft,
        },
    )
    assert approved.status_code == 200

    run_response = client.post(f"/projects/{project_id}/cash-flow/run", json={})
    assert run_response.status_code == 200
    run_body = run_response.json()
    run_id = run_body["calculationRunId"]
    assert run_body["salesCollectionSchedule"][0]["contract"] == 100
    assert run_body["salesCollectionSchedule"][2]["interim"] == 600
    assert run_body["salesCollectionSchedule"][3]["balance"] == 300
    assert run_body["scheduleProvenance"]["salesCollection"]["sourceMode"] == "approved_schedule"
    assert run_body["scheduleProvenance"]["salesCollection"]["reportEligible"] is True
    assert run_body["scheduleProvenance"]["costSchedule"]["sourceMode"] == "benchmark_estimate"
    assert run_body["scheduleProvenance"]["costSchedule"]["reportEligible"] is False
    assert run_body["scheduleProvenance"]["collectionAllocation"]["reportEligible"] is False
    assert run_body["scheduleProvenance"]["reportEligible"] is False
    assert run_body["reportFragments"]["reportEligible"] is False
    assert run_body["reportFragments"]["usageRestriction"] == "review_required_not_for_approval_or_final_report"

    runs = client.get(f"/projects/{project_id}/calculation-runs?engine_key=cash_flow_engine").json()["records"]
    persisted = next(record for record in runs if record["calculationRunId"] == run_id)
    input_snapshot = persisted["inputSnapshot"]
    product = input_snapshot["productMix"][0]
    assert len(product["collectionSteps"]) == 3
    assert product["collectionSourceLocator"]["cell"] == "A4:AY6"
    snapshot = persisted["resultSnapshot"]
    assert snapshot["salesCollectionSchedule"][2]["interim"] == 600

    client.delete(f"/projects/{project_id}")
