"""REALYTICS Agent Platform control-plane contracts and adapters.

This package is intentionally layered over the existing domain agents.  It does
not replace deterministic engines or the Shared Deal State Graph.
"""

from agent_platform.contracts import AgentManifest, AgentRequest, AgentResult
from agent_platform.registry import AgentRegistry, BrainRegistry

__all__ = [
    "AgentManifest",
    "AgentRegistry",
    "AgentRequest",
    "AgentResult",
    "BrainRegistry",
]
