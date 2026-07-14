from __future__ import annotations

from agent_platform.contracts import AgentRunStatus


TERMINAL_AGENT_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.COMPLETED_WITH_REVIEW,
        AgentRunStatus.BLOCKED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }
)

ALLOWED_AGENT_RUN_TRANSITIONS: dict[AgentRunStatus, frozenset[AgentRunStatus]] = {
    AgentRunStatus.RECEIVED: frozenset(
        {
            AgentRunStatus.CAPABILITY_CHECKED,
            AgentRunStatus.BLOCKED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.CAPABILITY_CHECKED: frozenset(
        {
            AgentRunStatus.PLANNED,
            AgentRunStatus.BLOCKED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.PLANNED: frozenset(
        {
            AgentRunStatus.BRAIN_SELECTED,
            AgentRunStatus.EXECUTING,
            AgentRunStatus.BLOCKED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.BRAIN_SELECTED: frozenset(
        {
            AgentRunStatus.EXECUTING,
            AgentRunStatus.BLOCKED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.EXECUTING: frozenset(
        {
            AgentRunStatus.VALIDATING,
            AgentRunStatus.RETRYING,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.RETRYING: frozenset(
        {
            AgentRunStatus.EXECUTING,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.VALIDATING: frozenset(
        {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.COMPLETED_WITH_REVIEW,
            AgentRunStatus.WAITING_FOR_HUMAN,
            AgentRunStatus.BLOCKED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.WAITING_FOR_HUMAN: frozenset(
        {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.COMPLETED_WITH_REVIEW,
            AgentRunStatus.BLOCKED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
}


class InvalidAgentRunTransition(RuntimeError):
    pass


def ensure_agent_run_transition(current: AgentRunStatus, target: AgentRunStatus) -> None:
    if current in TERMINAL_AGENT_RUN_STATUSES:
        raise InvalidAgentRunTransition(f"Terminal Agent run cannot transition: {current.value} -> {target.value}")
    if target not in ALLOWED_AGENT_RUN_TRANSITIONS.get(current, frozenset()):
        raise InvalidAgentRunTransition(f"Invalid Agent run transition: {current.value} -> {target.value}")

