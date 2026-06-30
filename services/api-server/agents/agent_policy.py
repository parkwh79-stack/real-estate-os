from __future__ import annotations

"""Runtime loader for LLM agent policies (agents/llm_agent_policies.md).

Each LLM touch-point calls ``system_prompt_for(agent_key)`` to get a system prompt
built from the COMMON policy section plus that agent's section. Editing the .md
changes LLM behaviour without code changes (the user's "agents check their md"
design), while keeping deterministic calculation agents untouched.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict

POLICY_FILE = Path(__file__).resolve().parent / "llm_agent_policies.md"
_SECTION_RE = re.compile(r"^##\s*\[agent:([a-z_]+)\]\s*$", re.MULTILINE)


@lru_cache(maxsize=1)
def _load_sections() -> Dict[str, str]:
    """Parse the policy md into {agent_key: section_text}. Cached; resilient to a missing file."""
    try:
        text = POLICY_FILE.read_text(encoding="utf-8")
    except OSError:
        return {}
    sections: Dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        key = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[key] = text[start:end].strip()
    return sections


def reload_policies() -> None:
    """Drop the cache so edits to the md take effect without a restart."""
    _load_sections.cache_clear()


def system_prompt_for(agent_key: str, *, base: str | None = None) -> str:
    """Build a system prompt: optional base + COMMON policy + the agent's section.

    Falls back gracefully (returns base or a minimal instruction) if the md or a
    section is absent, so a missing policy never breaks an LLM call.
    """
    sections = _load_sections()
    parts = []
    if base:
        parts.append(base.strip())
    common = sections.get("common")
    if common:
        parts.append(common)
    section = sections.get(agent_key)
    if section:
        parts.append(section)
    if not parts:
        return base or "You are a precise assistant. Output only what the source supports."
    return "\n\n".join(parts)
