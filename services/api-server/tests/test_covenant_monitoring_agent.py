from agents.covenant_monitoring_agent import CovenantMonitoringAgent


def test_all_ok_when_within_thresholds():
    result = CovenantMonitoringAgent().monitor({
        "dscr": 1.5, "ltv": 0.55, "ltc": 0.70, "exit_sale_rate": 0.60, "sales_rate": 0.80,
    })
    assert result["overallStatus"] == "ok"
    assert result["alerts"] == []
    assert result["summary"]["checkedCount"] == 5


def test_breach_on_dscr_floor_and_ltv_ceiling():
    result = CovenantMonitoringAgent().monitor({
        "dscr": 1.0,   # < 1.20 floor -> breach
        "ltv": 0.82,   # > 0.70 ceiling -> breach
        "ltc": 0.70, "exit_sale_rate": 0.60, "sales_rate": 0.80,
    })
    assert result["overallStatus"] == "breach"
    by_key = {c["key"]: c for c in result["checks"]}
    assert by_key["dscr"]["status"] == "breach"
    assert by_key["ltv"]["status"] == "breach"
    codes = {a["code"] for a in result["alerts"]}
    assert "COVENANT_DSCR_BREACH" in codes and "COVENANT_LTV_BREACH" in codes
    assert all(a["recommendedAction"] for a in result["alerts"])


def test_watch_near_threshold():
    # LTV 0.69 is within 5% of the 0.70 ceiling (0.665..0.70) -> watch
    result = CovenantMonitoringAgent().monitor({"ltv": 0.69, "dscr": 1.5})
    ltv = next(c for c in result["checks"] if c["key"] == "ltv")
    assert ltv["status"] == "watch"
    assert result["overallStatus"] == "watch"


def test_threshold_override():
    result = CovenantMonitoringAgent().monitor({"dscr": 1.3}, thresholds={"dscr": 1.4})
    dscr = next(c for c in result["checks"] if c["key"] == "dscr")
    assert dscr["threshold"] == 1.4
    assert dscr["status"] == "breach"  # 1.3 < 1.4


def test_stress_senior_break_raises_critical_alert():
    result = CovenantMonitoringAgent().monitor(
        {"dscr": 1.5, "ltv": 0.5},
        stress_test={"seniorBreakScenarioKey": "severe"},
    )
    assert result["overallStatus"] == "breach"
    assert any(a["code"] == "STRESS_SENIOR_PRINCIPAL_LOSS" for a in result["alerts"])


def test_dscr_zero_is_not_applicable_not_breach():
    # Sale/development PF with no NOI -> DSCR computes to 0; must be N/A, not breach.
    result = CovenantMonitoringAgent().monitor({
        "dscr": 0.0, "ltv": 0.54, "ltc": 0.59, "exit_sale_rate": 0.64,
    })
    keys = {c["key"] for c in result["checks"]}
    assert "dscr" not in keys
    assert "dscr" in result["notApplicable"]
    assert result["overallStatus"] == "ok"  # no false breach from DSCR=0


def test_missing_metrics_skipped():
    result = CovenantMonitoringAgent().monitor({"dscr": None, "ltv": "n/a"})
    assert result["summary"]["checkedCount"] == 0
    assert result["overallStatus"] == "no_data"
