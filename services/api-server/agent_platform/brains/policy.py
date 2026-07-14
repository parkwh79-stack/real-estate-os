from __future__ import annotations

from agent_platform.contracts import BrainRoutingPolicyRecord
from agent_platform.operational import OperationalRepository


class BrainRoutingPolicyError(RuntimeError):
    pass


class BrainRoutingPolicyService:
    """Maintains immutable, monotonic tenant/user routing-policy versions."""

    def __init__(self, repository: OperationalRepository) -> None:
        self._repository = repository

    def save(self, policy: BrainRoutingPolicyRecord) -> BrainRoutingPolicyRecord:
        existing_by_id = self._repository.get(BrainRoutingPolicyRecord, policy.policy_id)
        if existing_by_id:
            if existing_by_id == policy:
                return existing_by_id
            raise BrainRoutingPolicyError("BRAIN_ROUTING_POLICY_IMMUTABLE")

        scope = [
            item for item in self._repository.list(
                BrainRoutingPolicyRecord,
                filters={"tenant_id": policy.tenant_id},
                limit=1000,
            )
            if item.user_id == policy.user_id
        ]
        expected_version = max((item.version for item in scope), default=0) + 1
        if policy.version != expected_version:
            raise BrainRoutingPolicyError(
                f"BRAIN_ROUTING_POLICY_VERSION_EXPECTED:{expected_version}"
            )
        active = [item for item in scope if item.active]
        expected_supersedes = active[0].policy_id if active else None
        if expected_supersedes and policy.supersedes_policy_id != expected_supersedes:
            raise BrainRoutingPolicyError("BRAIN_ROUTING_POLICY_SUPERSEDES_REQUIRED")
        for previous in active:
            self._repository.save(previous.model_copy(update={"active": False}))
        return self._repository.save(policy)
