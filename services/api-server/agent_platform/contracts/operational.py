from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_platform.contracts.enums import (
    CodingPodJobStatus,
    DataClassification,
    LearningCandidateStatus,
    OutboxEventStatus,
    ProviderHealthStatus,
    ToolCallStatus,
    ToolSideEffectLevel,
)


class OperationalContract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RuntimeOutboxEvent(OperationalContract):
    schema_version: str = Field("realytics-runtime-outbox-event-v1", alias="schemaVersion")
    event_id: str = Field(..., alias="eventId")
    tenant_id: str = Field(..., alias="tenantId")
    project_id: str | None = Field(None, alias="projectId")
    aggregate_type: str = Field(..., alias="aggregateType")
    aggregate_id: str = Field(..., alias="aggregateId")
    event_type: str = Field(..., alias="eventType")
    idempotency_key: str = Field(..., alias="idempotencyKey")
    status: OutboxEventStatus = OutboxEventStatus.PENDING
    payload: dict[str, Any] = Field(default_factory=dict)
    attempt_count: int = Field(0, ge=0, alias="attemptCount")
    max_attempts: int = Field(8, ge=1, alias="maxAttempts")
    available_at: datetime = Field(..., alias="availableAt")
    lease_owner: str | None = Field(None, alias="leaseOwner")
    lease_expires_at: datetime | None = Field(None, alias="leaseExpiresAt")
    last_error: str | None = Field(None, alias="lastError")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    published_at: datetime | None = Field(None, alias="publishedAt")


class BrainProviderHealth(OperationalContract):
    schema_version: str = Field("realytics-brain-provider-health-v1", alias="schemaVersion")
    health_id: str = Field(..., alias="healthId")
    tenant_id: str = Field(..., alias="tenantId")
    brain_profile_id: str = Field(..., alias="brainProfileId")
    provider: str
    model: str
    status: ProviderHealthStatus = ProviderHealthStatus.UNKNOWN
    consecutive_failures: int = Field(0, ge=0, alias="consecutiveFailures")
    latency_ms: int | None = Field(None, ge=0, alias="latencyMs")
    failure_code: str | None = Field(None, alias="failureCode")
    failure_message: str | None = Field(None, alias="failureMessage")
    checked_at: datetime = Field(..., alias="checkedAt")
    next_check_at: datetime | None = Field(None, alias="nextCheckAt")
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrainRoutingPolicyRecord(OperationalContract):
    schema_version: str = Field("realytics-brain-routing-policy-v1", alias="schemaVersion")
    policy_id: str = Field(..., alias="policyId")
    tenant_id: str = Field(..., alias="tenantId")
    user_id: str | None = Field(None, alias="userId")
    version: int = Field(..., ge=1)
    active: bool = True
    allowed_providers: list[str] = Field(default_factory=list, alias="allowedProviders")
    prohibited_providers: list[str] = Field(default_factory=list, alias="prohibitedProviders")
    preferred_profile_ids: list[str] = Field(default_factory=list, alias="preferredProfileIds")
    max_cost_usd: float | None = Field(None, ge=0, alias="maxCostUsd")
    local_only: bool = Field(False, alias="localOnly")
    fail_closed: bool = Field(True, alias="failClosed")
    created_by: str = Field(..., alias="createdBy")
    created_at: datetime = Field(..., alias="createdAt")
    supersedes_policy_id: str | None = Field(None, alias="supersedesPolicyId")
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrainRoutingEventRecord(OperationalContract):
    schema_version: str = Field("realytics-brain-routing-event-v1", alias="schemaVersion")
    routing_event_id: str = Field(..., alias="routingEventId")
    run_id: str | None = Field(None, alias="runId")
    request_id: str | None = Field(None, alias="requestId")
    tenant_id: str = Field(..., alias="tenantId")
    user_id: str = Field(..., alias="userId")
    project_id: str | None = Field(None, alias="projectId")
    task_type: str = Field(..., alias="taskType")
    policy_id: str | None = Field(None, alias="policyId")
    policy_version: int | None = Field(None, ge=1, alias="policyVersion")
    selected_profile_id: str | None = Field(None, alias="selectedProfileId")
    selected_provider: str | None = Field(None, alias="selectedProvider")
    selected_model: str | None = Field(None, alias="selectedModel")
    blocked: bool = False
    blocked_reason: str | None = Field(None, alias="blockedReason")
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    occurred_at: datetime = Field(..., alias="occurredAt")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolManifest(OperationalContract):
    tool_id: str = Field(..., alias="toolId")
    version: str
    display_name: str = Field(..., alias="displayName")
    description: str
    input_schema_name: str = Field(..., alias="inputSchemaName")
    output_schema_name: str = Field(..., alias="outputSchemaName")
    side_effect_level: ToolSideEffectLevel = Field(..., alias="sideEffectLevel")
    supported_data_classifications: list[DataClassification] = Field(..., alias="supportedDataClassifications")
    required_roles: list[str] = Field(default_factory=list, alias="requiredRoles")
    requires_human_approval: bool = Field(False, alias="requiresHumanApproval")
    timeout_seconds: int = Field(30, ge=1, alias="timeoutSeconds")
    idempotent: bool = True
    active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolRequest(OperationalContract):
    tool_call_id: str = Field(..., alias="toolCallId")
    run_id: str = Field(..., alias="runId")
    step_id: str | None = Field(None, alias="stepId")
    tenant_id: str = Field(..., alias="tenantId")
    user_id: str = Field(..., alias="userId")
    project_id: str | None = Field(None, alias="projectId")
    tool_id: str = Field(..., alias="toolId")
    tool_version: str | None = Field(None, alias="toolVersion")
    data_classification: DataClassification = Field(..., alias="dataClassification")
    actor_roles: list[str] = Field(default_factory=list, alias="actorRoles")
    payload: dict[str, Any]
    idempotency_key: str = Field(..., alias="idempotencyKey")
    approval_id: str | None = Field(None, alias="approvalId")
    requested_at: datetime = Field(..., alias="requestedAt")


class ToolResult(OperationalContract):
    tool_call_id: str = Field(..., alias="toolCallId")
    status: ToolCallStatus
    output: dict[str, Any] | None = None
    error_code: str | None = Field(None, alias="errorCode")
    error_message: str | None = Field(None, alias="errorMessage")
    started_at: datetime | None = Field(None, alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(OperationalContract):
    schema_version: str = Field("realytics-tool-call-v1", alias="schemaVersion")
    request: ToolRequest
    result: ToolResult
    tool_version: str = Field(..., alias="toolVersion")
    side_effect_level: ToolSideEffectLevel = Field(..., alias="sideEffectLevel")
    state_version: int = Field(1, ge=1, alias="stateVersion")


class LearningCandidate(OperationalContract):
    schema_version: str = Field("realytics-learning-candidate-v1", alias="schemaVersion")
    candidate_id: str = Field(..., alias="candidateId")
    tenant_id: str = Field(..., alias="tenantId")
    project_id: str | None = Field(None, alias="projectId")
    candidate_type: str = Field(..., alias="candidateType")
    target_key: str = Field(..., alias="targetKey")
    proposed_change: dict[str, Any] = Field(..., alias="proposedChange")
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")
    source_run_ids: list[str] = Field(default_factory=list, alias="sourceRunIds")
    status: LearningCandidateStatus = LearningCandidateStatus.DRAFT
    created_by: str = Field(..., alias="createdBy")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)


class LearningEvaluation(OperationalContract):
    schema_version: str = Field("realytics-learning-evaluation-v1", alias="schemaVersion")
    evaluation_id: str = Field(..., alias="evaluationId")
    candidate_id: str = Field(..., alias="candidateId")
    tenant_id: str = Field(..., alias="tenantId")
    evaluation_type: str = Field(..., alias="evaluationType")
    baseline_metrics: dict[str, float] = Field(default_factory=dict, alias="baselineMetrics")
    candidate_metrics: dict[str, float] = Field(default_factory=dict, alias="candidateMetrics")
    passed: bool
    findings: list[str] = Field(default_factory=list)
    evaluated_by: str = Field(..., alias="evaluatedBy")
    evaluated_at: datetime = Field(..., alias="evaluatedAt")


class LearningPromotionDecision(OperationalContract):
    schema_version: str = Field("realytics-learning-promotion-v1", alias="schemaVersion")
    decision_id: str = Field(..., alias="decisionId")
    candidate_id: str = Field(..., alias="candidateId")
    tenant_id: str = Field(..., alias="tenantId")
    action: str
    environment: str
    reason: str
    approved_by: str = Field(..., alias="approvedBy")
    approved_at: datetime = Field(..., alias="approvedAt")
    rollback_of_decision_id: str | None = Field(None, alias="rollbackOfDecisionId")

    @model_validator(mode="after")
    def validate_action(self) -> "LearningPromotionDecision":
        if self.action not in {"start_canary", "promote", "reject", "rollback"}:
            raise ValueError("Unsupported learning promotion action")
        return self


class CodingPodJob(OperationalContract):
    schema_version: str = Field("realytics-coding-pod-job-v1", alias="schemaVersion")
    job_id: str = Field(..., alias="jobId")
    tenant_id: str = Field(..., alias="tenantId")
    requested_by: str = Field(..., alias="requestedBy")
    workspace_ref: str = Field(..., alias="workspaceRef")
    branch_name: str = Field(..., alias="branchName")
    requested_change: str = Field(..., alias="requestedChange")
    allowed_paths: list[str] = Field(default_factory=list, alias="allowedPaths")
    status: CodingPodJobStatus = CodingPodJobStatus.REQUESTED
    idempotency_key: str = Field(..., alias="idempotencyKey")
    lease_owner: str | None = Field(None, alias="leaseOwner")
    lease_expires_at: datetime | None = Field(None, alias="leaseExpiresAt")
    heartbeat_at: datetime | None = Field(None, alias="heartbeatAt")
    attempt_count: int = Field(0, ge=0, alias="attemptCount")
    max_attempts: int = Field(3, ge=1, alias="maxAttempts")
    state_version: int = Field(1, ge=1, alias="stateVersion")
    result_artifact_ids: list[str] = Field(default_factory=list, alias="resultArtifactIds")
    error_message: str | None = Field(None, alias="errorMessage")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

    @model_validator(mode="after")
    def enforce_isolated_job_contract(self) -> "CodingPodJob":
        if not self.branch_name.startswith("codex/"):
            raise ValueError("Coding Pod jobs must use an isolated codex/ branch")
        if not self.allowed_paths:
            raise ValueError("Coding Pod jobs require an explicit allowedPaths boundary")
        return self
