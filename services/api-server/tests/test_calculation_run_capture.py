from use_cases.calculation_run_capture import (
    scenario_engine_run_payloads,
    stress_test_run_payload,
)


def _scenario_dump() -> dict:
    return {
        "scenarioSetId": "set-1",
        "waterfallScenarios": [{"scenarioId": "sale_70_disc_10"}, {"scenarioId": "sale_50_disc_20"}],
        "unsoldLtvMatrix": {
            "pfAmount": 350_000_000_000,
            "rows": [
                {"unsoldLtv": 0.62, "riskLevel": "normal"},
                {"unsoldLtv": 1.15, "riskLevel": "high"},
            ],
        },
        "trancheRecoveryMatrix": [
            {
                "scenarioId": "r1",
                "saleRate": 0.7,
                "discountRate": 0.0,
                "recoveryPool": 500.0,
                "tranches": [{"tranche": "Senior", "recoveryRatio": 1.0}],
            },
            {
                "scenarioId": "r2",
                "saleRate": 0.5,
                "discountRate": 0.2,
                "recoveryPool": 300.0,
                "tranches": [{"tranche": "Senior", "recoveryRatio": 0.72}],
            },
        ],
        "riskFlags": [
            {"code": "SENIOR_SHORTFALL", "severity": "critical", "message": "선순위 부족"},
            {"code": "UNSOLD_LTV_BREACH", "severity": "critical", "message": "미분양 LTV 초과"},
            {"code": "UNSOLD_LTV_HIGH", "severity": "medium", "message": "미분양 LTV 높음"},
            {"code": "TRIGGER_REQUIRED", "severity": "medium", "message": "트리거 필요"},
        ],
    }


def test_scenario_payloads_route_blocker_flags_to_their_engines():
    payloads = {p["engineKey"]: p for p in scenario_engine_run_payloads(_scenario_dump())}

    assert set(payloads) == {"waterfall", "unsold_ltv", "tranche_recovery"}
    # SENIOR_SHORTFALL -> waterfall run (파일럿 blocker 이력 요구사항)
    wf_codes = [f["code"] for f in payloads["waterfall"]["result"]["riskFlags"]]
    assert wf_codes == ["SENIOR_SHORTFALL"]
    # UNSOLD_LTV_BREACH -> unsold_ltv run
    ul = payloads["unsold_ltv"]["result"]
    assert [f["code"] for f in ul["riskFlags"]] == ["UNSOLD_LTV_BREACH", "UNSOLD_LTV_HIGH"]
    assert ul["summary"]["maxUnsoldLtv"] == 1.15
    assert ul["summary"]["breachFlagCount"] == 1
    assert ul["summary"]["pfAmount"] == 350_000_000_000
    # tranche recovery numeric summary from already-computed rows only
    tr = payloads["tranche_recovery"]["result"]["summary"]
    assert tr == {"scenarioCount": 2, "flagCount": 1, "minRecoveryPool": 300.0, "maxRecoveryPool": 500.0}
    assert payloads["tranche_recovery"]["result"]["metrics"]["senior"]["value"] == 0.72
    assert payloads["tranche_recovery"]["result"]["metrics"]["senior"]["sourceLocator"]["sourceType"] == "scenario_tranche_recovery_row"
    # provenance: every run references the same scenario set section
    assert payloads["waterfall"]["artifactRef"] == {
        "type": "scenario_set",
        "scenarioSetId": "set-1",
        "section": "waterfallScenarios",
    }


def test_scenario_payloads_engine_filter():
    payloads = scenario_engine_run_payloads(_scenario_dump(), engines=["unsold_ltv"])
    assert len(payloads) == 1
    assert payloads[0]["engineKey"] == "unsold_ltv"


def test_stress_payload_counts_discount_stages_and_keeps_flags():
    dump = {
        "scenarioCount": 4,
        "hurdleRate": 0.1,
        "worstCaseKey": "tail",
        "seniorBreakScenarioKey": "tail",
        "firstLossScenarioKey": "severe",
        "scenarios": [
            {"key": "base", "discountRate": 0.0},
            {"key": "mild", "discountRate": 0.0},
            {"key": "severe", "discountRate": 0.10, "saleRate": 0.60, "seniorRecoveryRatio": 0.95},
            {"key": "tail", "discountRate": 0.20, "saleRate": 0.45, "seniorRecoveryRatio": 0.80},
        ],
        "riskFlags": [{"code": "STRESS_SENIOR_SHORTFALL", "severity": "critical"}],
    }

    payload = stress_test_run_payload(dump)

    assert payload["engineKey"] == "discount_stress"
    assert payload["engineVersion"] == "discount_stress_v1"
    summary = payload["result"]["summary"]
    assert summary["discountStressStageCount"] == 2  # severe(10%) + tail(20%) 할인 스트레스
    assert summary["scenarioCount"] == 4
    assert payload["result"]["riskFlags"][0]["code"] == "STRESS_SENIOR_SHORTFALL"
    assert payload["artifactRef"]["worstCaseKey"] == "tail"
    assert payload["result"]["metrics"]["discount10.exitSaleRate"]["value"] == 0.60
    assert payload["result"]["metrics"]["discount10.exitSaleRate"]["sourceLocator"]["sourceType"] == "discount_stress_scenario"


def test_empty_dump_yields_zero_summaries_not_errors():
    payloads = scenario_engine_run_payloads({})
    assert {p["engineKey"] for p in payloads} == {"waterfall", "unsold_ltv", "tranche_recovery"}
    for p in payloads:
        assert p["result"]["riskFlags"] == []
    assert stress_test_run_payload({})["result"]["summary"]["scenarioCount"] == 0
    assert stress_test_run_payload({})["engineKey"] == "discount_stress"


def test_registry_payload_has_auditable_engine_and_source_contract():
    payload = scenario_engine_run_payloads(_scenario_dump(), engines=["waterfall"])[0]
    assert payload["engineKey"] == "waterfall"
    assert payload["engineVersion"] == "calculation_scenario_engine_v1"
    assert payload["artifactRef"]["section"] == "waterfallScenarios"
    assert payload["result"]["riskFlags"][0]["code"] == "SENIOR_SHORTFALL"


def test_scenario_registry_includes_waterfall_and_unsold_row_metrics():
    payloads = scenario_engine_run_payloads({
        "scenarioSetId": "sample",
        "waterfallScenarios": [{"scenarioId": "base", "availableCash": 100, "totalShortfall": 5}],
        "unsoldLtvMatrix": {"rows": [{"scenarioId": "base", "unsoldLtv": 0.82}]},
        "trancheRecoveryMatrix": [],
        "riskFlags": [],
    })
    waterfall = next(item for item in payloads if item["engineKey"] == "waterfall")
    unsold = next(item for item in payloads if item["engineKey"] == "unsold_ltv")
    assert "waterfall.base.availableCash" in waterfall["result"]["metrics"]
    assert "unsold_ltv.base.unsoldLtv" in unsold["result"]["metrics"]


def test_legacy_bonri_profile_exposes_sample_cell_locators():
    from agents.legacy_bonri_workbook_profile import (
        LEGACY_BONRI_PROFILE_ID,
        legacy_bonri_registry_context,
    )

    context = legacy_bonri_registry_context("19.12.11 bonri.xlsx")
    assert context["templateId"] == LEGACY_BONRI_PROFILE_ID
    assert context["fieldLocators"]["revenue.total"]["sheet"] == "사업수지"
    assert context["fieldLocators"]["revenue.total"]["cell"] == "D11"
    assert context["fieldLocators"]["cashflow.monthly_sales_rates"]["range"] == "J8:CH10"
