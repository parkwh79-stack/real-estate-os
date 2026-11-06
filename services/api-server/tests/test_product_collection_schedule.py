from openpyxl import Workbook

from agents.cash_flow_engine import run_cash_flow_engine
from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study
from agents.feasibility_workbook_importer import _realytics_product_monthly_sales_rate_scenarios


def test_imported_product_collection_schedule_reaches_cash_flow_engine():
    study = {
        "version": "realytics-feasibility-study-v2",
        "asset_type": "apartment",
        "metrics": {"total_revenue": 1000, "total_cost": 600},
        "raw_import": {
            "productMonthlySalesRateScenarios": [{
                "products": [{
                    "productType": "apartment",
                    "productRevenue": 1000,
                    "collectionSteps": [
                        {"stage": "contract", "dueMonth": 0, "collectionRate": 0.1, "monthlyCollectionAmounts": [100] + [0] * 3},
                        {"stage": "interim", "dueMonth": 2, "collectionRate": 0.6, "monthlyCollectionAmounts": [0, 0, 600, 0]},
                        {"stage": "balance", "dueMonth": 3, "collectionRate": 0.3, "monthlyCollectionAmounts": [0, 0, 0, 300]},
                    ],
                    "provenance": {"sourceType": "excel_range", "sheetName": "02B_collection_schedule", "cell": "A4:AY6"},
                }]
            }]
        },
        "schedule": [
            {"key": "construction_start", "label": "construction_start", "planned_date": "2026-01"},
            {"key": "completion", "label": "completion", "planned_date": "2026-04"},
        ],
        "revenue_items": [{"key": "sales", "label": "Sales", "category": "sales", "amount": 1000}],
        "cost_items": [{"key": "cost", "label": "Cost", "category": "construction", "amount": 600}],
    }

    request = build_cash_flow_run_request_from_feasibility_study(study)
    assert request["productMix"][0]["collectionSteps"]
    assert request["productMix"][0]["collectionSourceLocator"]["cell"] == "A4:AY6"
    result = run_cash_flow_engine(request)
    assert result["salesCollectionSchedule"][0]["contract"] == 100
    assert result["salesCollectionSchedule"][2]["interim"] == 600
    assert result["salesCollectionSchedule"][3]["balance"] == 300


def test_formula_workbook_collection_sheet_is_resolved_by_prefix_and_locator():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "02B_" + "\uC218\uB0A9\uC2A4\uCF00\uC904"
    for _ in range(3):
        sheet.append([])
    sheet.append(["apartment", "contract", 0, 0.1, 1000, 100, 100])

    scenarios = _realytics_product_monthly_sales_rate_scenarios(workbook, "fixture.xlsx", 1000)

    assert scenarios[0]["sourceSheet"] == "02B_" + "\uC218\uB0A9\uC2A4\uCF00\uC904"
    assert scenarios[0]["products"][0]["collectionSteps"][0]["stage"] == "contract"


def test_aggregate_asset_schedule_is_distributed_across_existing_product_types():
    study = {
        "version": "realytics-feasibility-study-v2",
        "asset_type": "apartment",
        "product_mix": [
            {"key": "apt-a", "asset_type": "apartment", "product_name": "A", "unit_count": 25},
            {"key": "apt-b", "asset_type": "apartment", "product_name": "B", "unit_count": 75},
        ],
        "metrics": {"total_revenue": 1000, "total_cost": 600},
        "cost_items": [{"key": "cost", "label": "Cost", "category": "construction", "amount": 600}],
        "raw_import": {
            "productMonthlySalesRateScenarios": [{
                "products": [{
                    "productType": "apartment",
                    "productRevenue": 1000,
                    "collectionSteps": [
                        {"stage": "contract", "monthlyCollectionAmounts": [100, 0, 0]},
                        {"stage": "interim", "monthlyCollectionAmounts": [0, 600, 0]},
                        {"stage": "balance", "monthlyCollectionAmounts": [0, 0, 300]},
                    ],
                    "provenance": {"sourceType": "excel_range", "sheetName": "02B_collection_schedule", "cell": "A4:AY6"},
                }]
            }]
        },
    }

    request = build_cash_flow_run_request_from_feasibility_study(study)
    result = run_cash_flow_engine(request)

    assert result["salesCollectionSchedule"][0]["contract"] == 100
    assert result["salesCollectionSchedule"][1]["interim"] == 600
    assert result["salesCollectionSchedule"][2]["balance"] == 300
