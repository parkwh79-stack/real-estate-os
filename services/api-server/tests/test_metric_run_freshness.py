from use_cases.approval_readiness_gate import _metric_uses_latest_calculation_run


def test_orphan_or_unknown_engine_never_counts_as_latest():
    assert not _metric_uses_latest_calculation_run({"calculationRunId": "c1", "key": "profit"}, {})
    assert not _metric_uses_latest_calculation_run({"calculationRunId": "c1", "key": "cash_flow_engine.maxMinus"}, {})


def test_metric_requires_exact_latest_engine_run():
    runs = {"cash_flow_engine": {"calculationRunId": "c2"}}
    assert not _metric_uses_latest_calculation_run({"calculationRunId": "c1", "key": "cash_flow_engine.maxMinus"}, runs)
    assert _metric_uses_latest_calculation_run({"calculationRunId": "c2", "key": "cash_flow_engine.maxMinus"}, runs)
