from fastapi.testclient import TestClient

from db.repositories.upload_archive_repository import UploadArchiveRepository
from main import app
from routers import projects
from use_cases.workbook_import_approval import feasibility_study_draft_hash


def _isolated_upload_archive(tmp_path, monkeypatch):
    repository = UploadArchiveRepository(
        api_server_root=tmp_path,
        archive_root=tmp_path / "uploads",
    )
    monkeypatch.setattr(projects, "upload_archive_repository", lambda: repository)
    return repository


def _register_validated_source(project_id, draft):
    workbook_bytes = (
        f"REALYTICS dummy approved-schedule workbook\n"
        f"project={project_id}\n"
        f"draft={feasibility_study_draft_hash(draft)}\n"
    ).encode("utf-8")
    archive_meta = projects.upload_archive_repository().archive_upload(
        project_id,
        workbook_bytes,
        "approved-schedule.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    source_hash = archive_meta["archiveId"]
    validation = {
        "status": "passed",
        "profile": "legacy-bonri-mixed-use-pf-feasibility",
        "sourceDocumentHash": source_hash,
        "formulaIntegrity": {
            "hasBlockingFormulaErrors": False,
            "hasExternalWorkbookReferences": False,
        },
        "requiredSheetChecks": [{"sheetName": "MonthlyCF", "present": True}],
        "requiredKeyCellChecks": [{"key": "total_revenue", "passed": True}],
        "reconciliationChecks": [{"name": "profit", "passed": True}],
        "sourceConflicts": [],
    }
    source = {
        **archive_meta,
        "originalFileName": "approved-schedule.xlsx",
        "originalFileHash": source_hash,
        "documentType": "feasibility_workbook",
        "validationStatus": "passed",
        "validation": validation,
        "feasibilityStudyV2DraftHash": feasibility_study_draft_hash(draft),
    }
    projects.source_document_repository().save_upload_metadata(project_id, source)
    return source, validation


def test_approved_feasibility_schedule_reaches_calculation_run_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approved-schedule.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    _isolated_upload_archive(tmp_path, monkeypatch)
    client = TestClient(app)

    created = client.post("/projects/", json={"project": {"name": "Approved Schedule E2E"}})
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": project_id,
        "project_name": "Approved Schedule E2E",
        "asset_type": "apartment",
        "program": {
            "gross_floor_area_sqm": {
                "key": "gross_floor_area_sqm",
                "label": "Gross floor area",
                "value": 1_000,
                "unit": "SQM",
            }
        },
        "schedule": [
            {"key": "construction_start", "label": "construction_start", "planned_date": "2026-01"},
            {"key": "completion", "label": "completion", "planned_date": "2026-04"},
        ],
        "revenue_items": [{"key": "sales", "label": "Sales", "category": "sales", "amount": 1000}],
        "cost_items": [{"key": "cost", "label": "Cost", "category": "construction", "amount": 600}],
        "monthly_cash_flow": [
            {
                "month_index": month,
                "period": f"2026-{month + 1:02d}",
                "cash_out": {"construction_cost": 150},
                "source_locator": {"sourceType": "excel_column", "sheetName": "MonthlyCF", "cell": f"{cell}10:{cell}20"},
            }
            for month, cell in enumerate(("B", "C", "D", "E"))
        ],
        "financing": [
            {
                "tranche_id": "tr-a",
                "name": "Tr.A",
                "priority": "senior",
                "amount": 600,
            }
        ],
        "metrics": {"total_revenue": 1000, "total_cost": 600},
        "raw_import": {
            "mappedCells": {
                "grossFloorArea": "Summary!B9",
                "totalRevenue": "Summary!B10",
                "totalCost": "Summary!B11",
                "pfAmount": "Summary!B12",
            },
            "governedFactBasis": {
                "revenue.total_sales_revenue": {
                    "vatTreatment": "included",
                    "amountScope": "gross",
                    "allocationBasis": "direct",
                    "currency": "KRW",
                },
                "cost.total_project_cost": {
                    "vatTreatment": "included",
                    "amountScope": "gross",
                    "allocationBasis": "direct",
                    "currency": "KRW",
                },
                "financing.pf_amount": {
                    "vatTreatment": "not_applicable",
                    "amountScope": "not_applicable",
                    "allocationBasis": "not_applicable",
                    "financingBalanceBasis": "commitment",
                    "currency": "KRW",
                },
            },
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
    source, validation = _register_validated_source(project_id, draft)

    candidate = client.post(
        f"/projects/{project_id}/feasibility-workbook/import-candidate",
        json={
            "importId": "approved-schedule-import",
            "feasibilityStudyV2Draft": draft,
            "sourceDocument": source,
            "validation": validation,
        },
    )
    assert candidate.status_code == 200

    approved = client.post(
        f"/projects/{project_id}/feasibility-workbook/approve",
        json={
            "importId": "approved-schedule-import",
            "approvedBy": "e2e-reviewer",
            "comment": "Approved product collection schedule for Cash Flow.",
            "feasibilityStudyV2Draft": draft,
            "sourceDocument": source,
            "validation": validation,
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
    assert run_body["scheduleProvenance"]["costSchedule"]["sourceMode"] == "approved_schedule"
    assert run_body["scheduleProvenance"]["costSchedule"]["reportEligible"] is True
    assert run_body["scheduleProvenance"]["collectionAllocation"]["reportEligible"] is False
    assert run_body["scheduleProvenance"]["reportEligible"] is False
    assert run_body["reportFragments"]["reportEligible"] is False
    assert run_body["reportFragments"]["usageRestriction"] == "review_required_not_for_approval_or_final_report"

    runs = client.get(f"/projects/{project_id}/calculation-runs?engine_key=cash_flow_engine").json()["records"]
    persisted = next(record for record in runs if record["calculationRunId"] == run_id)
    input_snapshot = persisted["inputSnapshot"]
    assert input_snapshot["sourceMetadata"]["approvalStatus"] == "approved"
    assert input_snapshot["costSchedule"]["rows"][0]["constructionCost"] == 150
    product = input_snapshot["productMix"][0]
    assert len(product["collectionSteps"]) == 3
    assert product["collectionSourceLocator"]["cell"] == "A4:AY6"
    snapshot = persisted["resultSnapshot"]
    assert snapshot["salesCollectionSchedule"][2]["interim"] == 600

    client.delete(f"/projects/{project_id}")
