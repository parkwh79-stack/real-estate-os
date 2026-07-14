from __future__ import annotations

import re
from threading import RLock

from agent_platform.contracts import AgentInterface, AgentManifest, DataClassification


class AgentRegistryError(RuntimeError):
    pass


def _version_key(version: str) -> tuple[tuple[int, object], ...]:
    parts = re.split(r"[.\-+]", str(version))
    return tuple((0, int(part)) if part.isdigit() else (1, part.lower()) for part in parts)


class AgentRegistry:
    """Thread-safe, version-aware registry for contract-compliant agents."""

    def __init__(self) -> None:
        self._agents: dict[tuple[str, str], AgentInterface] = {}
        self._lock = RLock()

    def register(self, agent: AgentInterface) -> None:
        manifest = getattr(agent, "manifest", None)
        if not isinstance(manifest, AgentManifest):
            raise AgentRegistryError("Agent implementation must expose a validated AgentManifest")
        key = (manifest.agent_id, manifest.version)
        with self._lock:
            if key in self._agents:
                raise AgentRegistryError(f"Agent already registered: {manifest.agent_id}@{manifest.version}")
            self._agents[key] = agent

    def unregister(self, agent_id: str, version: str) -> None:
        with self._lock:
            self._agents.pop((agent_id, version), None)

    def get(self, agent_id: str, version: str | None = None) -> AgentInterface:
        with self._lock:
            if version is not None:
                try:
                    return self._agents[(agent_id, version)]
                except KeyError as exc:
                    raise AgentRegistryError(f"Unknown agent: {agent_id}@{version}") from exc
            candidates = [
                agent
                for (registered_id, _), agent in self._agents.items()
                if registered_id == agent_id and agent.manifest.active
            ]
        if not candidates:
            raise AgentRegistryError(f"No active agent registered for: {agent_id}")
        return max(candidates, key=lambda item: _version_key(item.manifest.version))

    def find_candidates(
        self,
        *,
        task_type: str,
        asset_type: str | None,
        data_classification: DataClassification,
    ) -> list[AgentManifest]:
        with self._lock:
            manifests = [agent.manifest for agent in self._agents.values()]
        result = []
        for manifest in manifests:
            if not manifest.active or task_type not in manifest.supported_task_types:
                continue
            if data_classification not in manifest.supported_data_classifications:
                continue
            if asset_type and manifest.supported_asset_types and asset_type not in manifest.supported_asset_types:
                continue
            result.append(manifest)
        return sorted(result, key=lambda item: (item.agent_id, _version_key(item.version)), reverse=False)

    def list_active(self) -> list[AgentManifest]:
        with self._lock:
            manifests = [agent.manifest for agent in self._agents.values() if agent.manifest.active]
        return sorted(manifests, key=lambda item: (item.agent_id, _version_key(item.version)))

    def snapshot(self) -> list[dict[str, object]]:
        return [manifest.model_dump(by_alias=True, mode="json") for manifest in self.list_active()]
