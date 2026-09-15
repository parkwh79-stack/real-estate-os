from copy import deepcopy

import pytest

from agents.cash_flow_engine import run_cash_flow_engine
from db.repositories.calculation_run_repository import select_local_export_runs
from routers import projects
from use_cases.cash_flow_dependencies import dependent_inputs, financial_input_hash, scale_cash_flow_request


def request_data():
    return {
        "requestType": "cash_flow_run_request_v1",
        "project": {"projectId": "p"},
        "schedule": {"construction_start": "2026-01-01", "completion": "2026-04-01"},
        "sourcesAndUses": {"revenue": {"totalSalesRevenue": 200},
            "cost": {"landCost": 20, "directConstructionCost": 50, "financialCost": 10, "otherCost": 0, "totalProjectCost": 80}},
        "productMix": [{"type": "Apartment", "units": 2, "totalSalesRevenue": 200,
                        "monthlyCollectionAmounts": [0, 20, 30, 150]}],
        "pfStructure": [{"tranche": "Senior", "amount": 90, "repaymentPriority": 1, "interestRate": 0.05, "feeRate": 0}],
        "costSchedule": {"rows": [{"monthIndex": 1, "constructionCost": 50, "financialCost": 10}]},
    }


def test_projections_use_overridden_cf_totals_and_one_input_hash():
    base = request_data()
    result = run_cash_flow_engine(base)
    scenario, stress = dependent_inputs(base, result, 'anchor')
    assert scenario['productMix'][0]['totalRevenue'] == 200
    assert stress['totalRevenue'] == 200
    assert stress['totalCost'] == 80
    assert stress['pfAmount'] == 90
    assert stress['financialInputHash'] == scenario['financialInputHash'] == financial_input_hash(base)
    assert stress['baseCashFlowRequest'] == base
    assert stress['baseCashFlowRequest'] is not base


def test_stress_preserves_source_timing_and_never_mutates_base():
    base = request_data()
    original = deepcopy(base)
    changed = scale_cash_flow_request(base, revenue=100, construction=100, finance=20)
    assert changed['productMix'][0]['monthlyCollectionAmounts'] == [0, 10, 15, 75]
    assert changed['costSchedule']['rows'][0] == {'monthIndex': 1, 'constructionCost': 100, 'financialCost': 20}
    assert changed['schedule'] == base['schedule']
    assert changed['reportEligible'] is False
    assert base == original
    base['sourcesAndUses']['cost']['financialCost'] = 0
    with pytest.raises(ValueError, match='allocation_required'):
        scale_cash_flow_request(base, revenue=100, construction=50, finance=20)


def test_matching_parent_with_wrong_input_hash_is_ineligible():
    rows = [
        {'calculationRunId': 'a', 'engineKey': 'cash_flow_engine', 'financialInputHash': 'one'},
        {'calculationRunId': 'b', 'engineKey': 'waterfall', 'parentCalculationRunId': 'a', 'financialInputHash': 'two'},
    ]
    selection = select_local_export_runs(rows, dependent_engine_keys=['waterfall'])
    assert selection['reportEligible'] is False
    assert selection['ineligibleEngineKeys'] == ['waterfall']


def test_cf_endpoint_uses_request_and_exposes_dependent_failure(monkeypatch):
    monkeypatch.setattr(projects, 'PROJECTS_DB', {'p': {'id': 'p', 'feasibility_input': {'total_revenue': 999999}}})
    records = []
    def persist(pid, *, engine_key, result, input_data, **kwargs):
        records.append((engine_key, deepcopy(input_data)))
        return {'calculationRunId': f'run-{engine_key}'}
    monkeypatch.setattr(projects, '_persist_calculation_run', persist)
    result = projects.run_project_cash_flow('p', request_data())
    assert result['summary']['totalRevenue'] == 200
    assert result['dependentExecutionStatus'] == 'completed'
    assert len(records) == 5
    assert len({row[1]['financialInputHash'] for row in records}) == 1
    def fail(*args, **kwargs):
        raise RuntimeError('private DSN')
    monkeypatch.setattr(projects, '_persist_scenario_calculation_runs', fail)
    result = projects.run_project_cash_flow('p', request_data())
    assert result['dependentExecutionStatus'] == 'partial'
    assert result['dependentFailures'][0]['reason'] == 'dependent_scenario_failed'
    assert 'private' not in str(result)
