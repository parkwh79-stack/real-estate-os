from __future__ import annotations

from enum import Enum


class AgentRunStatus(str, Enum):
    RECEIVED = "received"
    CAPABILITY_CHECKED = "capability_checked"
    PLANNED = "planned"
    BRAIN_SELECTED = "brain_selected"
    EXECUTING = "executing"
    VALIDATING = "validating"
    RETRYING = "retrying"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    COMPLETED_WITH_REVIEW = "completed_with_review"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRunStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BrainUsageEventStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    FALLBACK = "fallback"


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceRequirement(str, Enum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"
    STRICT = "strict"


class ReviewRequirement(str, Enum):
    NONE = "none"
    CONDITIONAL = "conditional"
    REQUIRED = "required"


class ExecutionMode(str, Enum):
    DETERMINISTIC_ONLY = "deterministic_only"
    LLM_ONLY = "llm_only"
    DETERMINISTIC_FIRST = "deterministic_first"
    HYBRID = "hybrid"


class ImplementationLevel(str, Enum):
    PROTOTYPE = "prototype"
    CONTROLLED = "controlled"
    PRODUCTION_CRITICAL = "production_critical"


class OutboxEventStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRYING = "retrying"
    PUBLISHED = "published"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class ProviderHealthStatus(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ToolSideEffectLevel(str, Enum):
    READ_ONLY = "read_only"
    INTERNAL_WRITE = "internal_write"
    EXTERNAL_WRITE = "external_write"
    DESTRUCTIVE = "destructive"


class ToolCallStatus(str, Enum):
    REQUESTED = "requested"
    REQUIRES_APPROVAL = "requires_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LearningCandidateStatus(str, Enum):
    DRAFT = "draft"
    PENDING_EVALUATION = "pending_evaluation"
    EVALUATING = "evaluating"
    EVALUATED = "evaluated"
    REJECTED = "rejected"
    CANARY = "canary"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"


class CodingPodJobStatus(str, Enum):
    REQUESTED = "requested"
    VALIDATED = "validated"
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
