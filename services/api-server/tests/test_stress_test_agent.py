from agents.stress_test_agent import StressTestAgent
from schemas.stress_test import StressTestRequest, StressTranche


def _base_request(**overrides) -> StressTestRequest:
    data = dict(
        totalRevenue=2800.0,
        landCost=640.0,
        constructionCost=850.0,
        otherCost=150.0,
        financeCost=0.0,
        pfStructure=[
            StressTranche(tranche="Senior", amount=1200.0, priority=1, interestRate=0.058, feeRate=0.01),
            StressTranche(tranche="Mezzanine", amount=400.0, priority=2, interestRate=0.085, feeRate=0.015),
            StressTranche(tranche="Junior", amount=200.0, priority=3, interestRate=0.12, feeRate=0.02),
        ],
    )
    data.update(overrides)
    return StressTestRequest(**data)


def test_default_scenarios_produce_four_stages():
    result = StressTestAgent().run_stress_test(_base_request())
    assert result.scenarioCount == 4
    assert [s.key for s in result.scenarios] == ["base", "mild", "severe", "tail"]


def test_irr_degrades_across_stress_stages():
    result = StressTestAgent().run_stress_test(_base_request())
    irrs = [s.projectIrr if s.projectIrr is not None else float("-inf") for s in result.scenarios]
    for earlier, later in zip(irrs, irrs[1:]):
        assert later <= earlier


def test_base_case_is_normal_and_meets_or_beats_loss():
    base = StressTestAgent().run_stress_test(_base_request()).scenarios[0]
    assert base.key == "base"
    assert base.projectIrr is not None and base.projectIrr > 0
    assert base.seniorStatus == "normal"
    assert base.statusLabel == "정상"


def test_deep_stress_breaks_senior_principal():
    result = StressTestAgent().run_stress_test(
        _base_request(totalRevenue=1500.0, unsoldRecoveryHaircut=0.5)
    )
    tail = next(s for s in result.scenarios if s.key == "tail")
    assert tail.seniorStatus == "senior_principal_loss_possible"
    assert tail.statusLabel == "원금 일부 손실 가능"
    assert result.seniorBreakScenarioKey in {"severe", "tail"}
    assert tail.seniorRecoveryRatio is not None and tail.seniorRecoveryRatio < 1.0


def test_senior_not_evaluated_without_tranches():
    result = StressTestAgent().run_stress_test(_base_request(pfStructure=[]))
    assert all(s.seniorStatus == "not_evaluated" for s in result.scenarios)
    assert all(s.seniorRecoveryRatio is None for s in result.scenarios)


def test_custom_scenarios_and_report_fragment():
    result = StressTestAgent().run_stress_test(_base_request())
    assert "Stress Test" in result.reportFragment
    assert result.worstCaseKey == "tail"
    assert result.hurdleRate == 0.12
