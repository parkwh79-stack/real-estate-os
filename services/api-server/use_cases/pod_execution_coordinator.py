from __future__ import annotations

"""Dependency-aware execution policy for Chief Deal Orchestrator Pods.

The task graph is not merely presentation metadata.  This coordinator makes
its dependency edges enforceable for both single-Pod and full-graph runs.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

from schemas.orchestration import AgentPodPlan


READY_STATUSES = {"completed", "completed_with_warnings"}


@dataclass(frozen=True)
class PodDependencyGate:
    pod_key: str
    ready: bool
    dependencies: List[str]
    incomplete_dependencies: List[str]
    dependency_statuses: Dict[str, str]


class PodDependencyError(ValueError):
    def __init__(self, gate: PodDependencyGate):
        self.gate = gate
        details = ", ".join(
            f"{dependency}={gate.dependency_statuses.get(dependency, 'not_run')}"
            for dependency in gate.incomplete_dependencies
        )
        super().__init__(
            f"Pod {gate.pod_key} cannot run before its dependencies complete: {details}"
        )


class PodExecutionCoordinator:
    """Validate and order a track-specific Pod dependency graph."""

    def topological_order(
        self,
        pods: Sequence[AgentPodPlan],
        preferred_order: Iterable[str] | None = None,
    ) -> List[str]:
        by_key = {pod.pod_key: pod for pod in pods}
        if len(by_key) != len(pods):
            raise ValueError("Duplicate Pod keys in dependency graph")
        preferred = [key for key in (preferred_order or by_key.keys()) if key in by_key]
        rank = {key: index for index, key in enumerate(preferred)}
        indegree = {key: 0 for key in by_key}
        downstream: Dict[str, List[str]] = {key: [] for key in by_key}

        for pod in pods:
            for dependency in pod.dependencies:
                if dependency not in by_key:
                    raise ValueError(
                        f"Pod {pod.pod_key} references unknown dependency {dependency}"
                    )
                indegree[pod.pod_key] += 1
                downstream[dependency].append(pod.pod_key)

        ready = sorted(
            (key for key, degree in indegree.items() if degree == 0),
            key=lambda key: rank.get(key, len(rank)),
        )
        ordered: List[str] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for target in sorted(
                downstream[current], key=lambda key: rank.get(key, len(rank))
            ):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
                    ready.sort(key=lambda key: rank.get(key, len(rank)))

        if len(ordered) != len(by_key):
            unresolved = sorted(key for key, degree in indegree.items() if degree > 0)
            raise ValueError(
                "Pod dependency graph contains a cycle: " + ", ".join(unresolved)
            )
        return ordered

    def dependency_gate(
        self,
        pod: AgentPodPlan,
        pod_runs: Dict[str, Dict[str, Any]],
    ) -> PodDependencyGate:
        statuses = {
            dependency: str((pod_runs.get(dependency) or {}).get("status") or "not_run")
            for dependency in pod.dependencies
        }
        incomplete = [
            dependency
            for dependency, status in statuses.items()
            if status not in READY_STATUSES
        ]
        return PodDependencyGate(
            pod_key=pod.pod_key,
            ready=not incomplete,
            dependencies=list(pod.dependencies),
            incomplete_dependencies=incomplete,
            dependency_statuses=statuses,
        )

    def require_ready(
        self,
        pod: AgentPodPlan,
        pod_runs: Dict[str, Dict[str, Any]],
    ) -> PodDependencyGate:
        gate = self.dependency_gate(pod, pod_runs)
        if not gate.ready:
            raise PodDependencyError(gate)
        return gate

    def downstream_pods(
        self,
        pod_key: str,
        pods: Sequence[AgentPodPlan],
    ) -> List[str]:
        return [pod.pod_key for pod in pods if pod_key in pod.dependencies]
