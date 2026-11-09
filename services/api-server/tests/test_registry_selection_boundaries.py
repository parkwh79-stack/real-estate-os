from types import SimpleNamespace

import pytest

from agent_platform.bootstrap import build_default_agent_registry, POD_AI_REVIEW_AGENT_ID
from agent_platform.registry import AgentRegistry, AgentRegistryError, ToolRegistry
from agent_platform.tools import build_default_tool_registry


def test_explicit_agent_version_cannot_bypass_inactive_manifest():
    template = build_default_agent_registry().get(POD_AI_REVIEW_AGENT_ID)
    inactive = SimpleNamespace(manifest=template.manifest.model_copy(update={"active": False}))
    registry = AgentRegistry()
    registry.register(inactive)
    with pytest.raises(AgentRegistryError, match="Inactive agent"):
        registry.get(inactive.manifest.agent_id, inactive.manifest.version)


def test_tool_latest_version_uses_numeric_components():
    template = build_default_tool_registry().list_active()[0]
    registry = ToolRegistry()
    for version in ("1.9.0", "1.10.0", "1.2.0"):
        registry.register(SimpleNamespace(manifest=template.model_copy(update={"version": version})))
    assert registry.get(template.tool_id).manifest.version == "1.10.0"
    assert registry.get(template.tool_id, "1.9.0").manifest.version == "1.9.0"
