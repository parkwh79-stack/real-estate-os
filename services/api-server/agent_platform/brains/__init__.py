from agent_platform.brains.interface import BrainInterface
from agent_platform.brains.router import CapabilityBrainRouter
from agent_platform.brains.control import (
    brain_profile_from_ai_context,
    record_brain_provider_result,
    route_ai_connection_candidates,
    route_ai_connection_context,
)
from agent_platform.brains.policy import BrainRoutingPolicyError, BrainRoutingPolicyService


def __getattr__(name: str):
    # AIConnectionBrainAdapter imports agents.ai_connection, which in turn uses
    # the central control module. Lazy loading prevents that bridge from forming
    # an import cycle while preserving the public package API.
    if name in {"AIConnectionBrainAdapter", "BrainExecutionError", "MockBrain"}:
        from agent_platform.brains.ai_connection_adapter import (
            AIConnectionBrainAdapter,
            BrainExecutionError,
            MockBrain,
        )

        return {
            "AIConnectionBrainAdapter": AIConnectionBrainAdapter,
            "BrainExecutionError": BrainExecutionError,
            "MockBrain": MockBrain,
        }[name]
    raise AttributeError(name)


__all__ = [
    "AIConnectionBrainAdapter",
    "BrainExecutionError",
    "BrainInterface",
    "BrainRoutingPolicyError",
    "BrainRoutingPolicyService",
    "CapabilityBrainRouter",
    "MockBrain",
    "brain_profile_from_ai_context",
    "record_brain_provider_result",
    "route_ai_connection_candidates",
    "route_ai_connection_context",
]
