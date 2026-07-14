from __future__ import annotations

from threading import RLock

from agent_platform.contracts import BrainInterface, BrainProfile


class BrainRegistryError(RuntimeError):
    pass


class BrainRegistry:
    """Stores Brain adapters by profile ID; profiles never contain raw API keys."""

    def __init__(self) -> None:
        self._brains: dict[str, BrainInterface] = {}
        self._lock = RLock()

    def register(self, brain: BrainInterface) -> None:
        profile = getattr(brain, "profile", None)
        if not isinstance(profile, BrainProfile):
            raise BrainRegistryError("Brain adapter must expose a validated BrainProfile")
        with self._lock:
            if profile.brain_profile_id in self._brains:
                raise BrainRegistryError(f"Brain profile already registered: {profile.brain_profile_id}")
            self._brains[profile.brain_profile_id] = brain

    def unregister(self, profile_id: str) -> None:
        with self._lock:
            self._brains.pop(profile_id, None)

    def get(self, profile_id: str) -> BrainInterface:
        with self._lock:
            try:
                return self._brains[profile_id]
            except KeyError as exc:
                raise BrainRegistryError(f"Unknown Brain profile: {profile_id}") from exc

    def list_enabled(self) -> list[BrainInterface]:
        with self._lock:
            values = [brain for brain in self._brains.values() if brain.profile.enabled]
        return sorted(values, key=lambda item: item.profile.brain_profile_id)

    def list_profiles(self) -> list[BrainProfile]:
        return [brain.profile for brain in self.list_enabled()]
