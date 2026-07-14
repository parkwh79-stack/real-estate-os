from __future__ import annotations

from threading import RLock

from agent_platform.contracts import ToolInterface, ToolManifest


class ToolRegistryError(RuntimeError):
    pass


class ToolRegistry:
    """Version-aware registry that exposes only contract-validated tools."""

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], ToolInterface] = {}
        self._lock = RLock()

    def register(self, tool: ToolInterface) -> None:
        manifest = getattr(tool, "manifest", None)
        if not isinstance(manifest, ToolManifest):
            raise ToolRegistryError("Tool implementation must expose a validated ToolManifest")
        key = (manifest.tool_id, manifest.version)
        with self._lock:
            if key in self._tools:
                raise ToolRegistryError(f"Tool already registered: {manifest.tool_id}@{manifest.version}")
            self._tools[key] = tool

    def get(self, tool_id: str, version: str | None = None) -> ToolInterface:
        with self._lock:
            candidates = [
                tool for (registered_id, registered_version), tool in self._tools.items()
                if registered_id == tool_id
                and tool.manifest.active
                and (version is None or registered_version == version)
            ]
        if not candidates:
            suffix = f"@{version}" if version else ""
            raise ToolRegistryError(f"Unknown active tool: {tool_id}{suffix}")
        return sorted(candidates, key=lambda item: item.manifest.version)[-1]

    def list_active(self) -> list[ToolManifest]:
        with self._lock:
            manifests = [tool.manifest for tool in self._tools.values() if tool.manifest.active]
        return sorted(manifests, key=lambda item: (item.tool_id, item.version))
