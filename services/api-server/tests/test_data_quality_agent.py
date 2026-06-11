from agents.data_quality_agent import DataQualityAgent


def test_data_quality_agent_detects_revenue_pf_and_area_conflicts():
    result = DataQualityAgent().review(
        {
            "project": {"name": "DQ Project"},
            "land": {"area_sqm": 1000},
            "architectural_plan": {
                "land_area_sqm": 1100,
                "gross_floor_area_sqm": 5000,
                "unit_count": 10,
            },
            "feasibility_input": {
                "total_revenue": 100_000,
                "total_revenue_after_vat": 110_000,
            },
            "pf_structure": [
                {"tranche": "Tr.A", "amount": 60_000},
                {"tranche": "Tr.A", "amount": 10_000},
            ],
            "expected_results": {"total_pf_amount": 50_000},
            "product_mix": [
                {
                    "units": 8,
                    "totalRevenue": 70_000,
                    "contractAreaSqm": 100,
                    "contractAreaPy": 20,
                    "vatIncluded": True,
                }
            ],
            "document_market_evidence": {
                "productMix": [
                    {"units": 9, "totalRevenue": 72_000, "vatIncluded": False}
                ]
            },
        },
        {
            "developerFeasibility": {"revenue": {"totalRevenue": 100_000}},
            "marketabilityAnalysis": {"projectSummary": {"totalSalesRevenue": 70_000}},
        },
    )

    assert result["analysisType"] == "data_quality_review"
    assert result["status"] == "blocking"
    keys = {check["key"] for check in result["checks"] if check["status"] == "blocking"}
    assert "land_area_consistency" in keys
    assert "sales_revenue_consistency" in keys
    assert "pf_amount_consistency" in keys
    assert "sqm_py_conversion_consistency" in keys


def test_data_quality_agent_passes_clear_consistent_numbers():
    result = DataQualityAgent().review(
        {
            "project": {"name": "DQ Clean"},
            "land": {"area_sqm": 1000},
            "architectural_plan": {
                "land_area_sqm": 1000,
                "unit_count": 10,
            },
            "feasibility_input": {"total_revenue": 100_000},
            "pf_structure": [{"tranche": "Tr.A", "amount": 60_000}],
            "expected_results": {"total_pf_amount": 60_000},
            "product_mix": [
                {
                    "units": 10,
                    "totalRevenue": 100_000,
                    "contractAreaSqm": 100,
                    "contractAreaPy": 30.25,
                    "vatIncluded": True,
                }
            ],
        },
        {
            "developerFeasibility": {"revenue": {"totalRevenue": 100_000}},
            "marketabilityAnalysis": {"projectSummary": {"totalSalesRevenue": 100_000}},
        },
    )

    assert result["summary"]["blockingCount"] == 0
    assert not result["blockingIssues"]
    assert any(check["key"] == "sales_revenue_consistency" and check["status"] == "clear" for check in result["checks"])


def test_data_quality_agent_applies_approved_assumption_override():
    result = DataQualityAgent().review(
        {
            "project": {"name": "DQ Override"},
            "feasibility_input": {"total_revenue": 100_000},
            "product_mix": [{"units": 10, "totalRevenue": 70_000}],
            "approvedAssumptionOverrides": [
                {
                    "overrideId": "ovr-sales-revenue",
                    "checkKey": "sales_revenue_consistency",
                    "canonicalField": "feasibility_input.total_revenue",
                    "approvedValue": 100_000,
                    "unit": "KRW",
                    "approvedBy": "credit_officer",
                    "approvedAt": "2026-06-01T00:00:00+09:00",
                    "reason": "Use signed feasibility workbook total revenue as the authoritative value.",
                    "status": "active",
                }
            ],
        },
        {
            "developerFeasibility": {"revenue": {"totalRevenue": 100_000}},
            "marketabilityAnalysis": {"projectSummary": {"totalSalesRevenue": 70_000}},
        },
    )

    sales_check = next(check for check in result["checks"] if check["key"] == "sales_revenue_consistency")
    assert result["status"] == "needs_review"
    assert sales_check["status"] == "overridden"
    assert sales_check["originalStatus"] == "blocking"
    assert sales_check["approvedAssumptionOverride"]["overrideId"] == "ovr-sales-revenue"
    assert result["summary"]["overriddenCount"] == 1
    assert result["appliedOverrides"][0]["canonicalField"] == "feasibility_input.total_revenue"
