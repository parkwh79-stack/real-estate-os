from types import SimpleNamespace

import pytest

from use_cases.pod_execution_coordinator import PodDependencyError, PodExecutionCoordinator


def _pod(key: str, dependencies: list[str]):
    return SimpleNamespace(pod_key=key, dependencies=dependencies)


def test_topological_order_enforces_dependencies_over_preferred_order():
    coordinator = PodExecutionCoordinator()
    pods = [
        _pod("land_legal", []),
        _pod("market_product", ["land_legal"]),
        _pod("cost_schedule", ["land_legal"]),
        _pod("feasibility_cashflow", ["market_product", "cost_schedule"]),
        _pod("pf_credit", ["feasibility_cashflow"]),
    ]

    order = coordinator.topological_order(
        pods,
        ["pf_credit", "feasibility_cashflow", "cost_schedule", "market_product", "land_legal"],
    )

    assert order.index("land_legal") < order.index("market_product")
    assert order.index("land_legal") < order.index("cost_schedule")
    assert order.index("market_product") < order.index("feasibility_cashflow")
    assert order.index("cost_schedule") < order.index("feasibility_cashflow")
    assert order.index("feasibility_cashflow") < order.index("pf_credit")


def test_dependency_gate_rejects_unfinished_upstream_pod():
    coordinator = PodExecutionCoordinator()
    target = _pod("market_product", ["land_legal"])

    with pytest.raises(PodDependencyError) as exc_info:
        coordinator.require_ready(target, {"land_legal": {"status": "running"}})

    assert exc_info.value.gate.incomplete_dependencies == ["land_legal"]


def test_topological_order_rejects_cycles():
    coordinator = PodExecutionCoordinator()
    with pytest.raises(ValueError, match="cycle"):
        coordinator.topological_order([_pod("a", ["b"]), _pod("b", ["a"])])
