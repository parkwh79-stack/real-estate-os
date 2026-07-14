from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from agent_platform.contracts.enums import (
    AgentRunStatus,
    AgentRunStepStatus,
    BrainUsageEventStatus,
    DataClassification,
    EvidenceRequirement,
    ExecutionMode,
    ImplementationLevel,
    ReviewRequirement,
    RiskLevel,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CapabilityRequirement(ContractModel):
    name: str
    minimum_level: str | None = Field(None, alias="minimumLevel")
    required: bool = True


class ExecutionPolicy(ContractModel):
    mode: ExecutionMode = ExecutionMode.HYBRID
    llm_required: bool = Field(False, alias="llmRequired")
    human_review: ReviewRequirement = Field(ReviewRequirement.CONDITIONAL, alias="humanReview")
    evidence_requirement: EvidenceRequirement = Field(EvidenceRequirement.REQUIRED, alias="evidenceRequirement")
    deterministic_authority_required: bool = Field(False, alias="deterministicAuthorityRequired")
    allow_parallel_tools: bool = Field(False, alias="allowParallelTools")
    allow_parallel_agents: bool = Field(False, alias="allowParallelAgents")
    max_execution_seconds: int = Field(120, ge=1, alias="maxExecutionSeconds")
    max_tool_calls: int = Field(20, ge=0, alias="maxToolCalls")
    max_llm_calls: int = Field(3, ge=0, alias="maxLlmCalls")

    @model_validator(mode="after")
    def prohibit_llm_as_authoritative_engine(self) -> "ExecutionPolicy":
        if self.mode == ExecutionMode.LLM_ONLY and self.deterministic_authority_required:
            raise ValueError("LLM_ONLY cannot produce authoritative deterministic output")
        return self


class FailurePolicy(ContractModel):
    max_retries: int = Field(2, ge=0, alias="maxRetries")
    retryable_error_codes: list[str] = Field(default_factory=list, alias="retryableErrorCodes")
    deterministic_fallback: bool = Field(False, alias="deterministicFallback")
    alternate_brain_fallback: bool = Field(True, alias="alternateBrainFallback")
    fail_closed: bool = Field(True, alias="failClosed")


class AgentManifest(ContractModel):
    agent_id: str = Field(..., alias="agentId")
    version: str
    display_name: str = Field(..., alias="displayName")
    domain: str
    role: str
    description: str
    active: bool = True
    implementation_level: ImplementationLevel = Field(ImplementationLevel.CONTROLLED, alias="implementationLevel")
    supported_asset_types: list[str] = Field(default_factory=list, alias="supportedAssetTypes")
    supported_task_types: list[str] = Field(default_factory=list, alias="supportedTaskTypes")
    input_schema_name: str = Field(..., alias="inputSchemaName")
    input_schema_version: str = Field(..., alias="inputSchemaVersion")
    output_schema_name: str = Field(..., alias="outputSchemaName")
    output_schema_version: str = Field(..., alias="outputSchemaVersion")
    required_capabilities: list[CapabilityRequirement] = Field(default_factory=list, alias="requiredCapabilities")
    required_tools: list[str] = Field(default_factory=list, alias="requiredTools")
    optional_tools: list[str] = Field(default_factory=list, alias="optionalTools")
    supported_data_classifications: list[DataClassification] = Field(..., alias="supportedDataClassifications")
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy, alias="executionPolicy")
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy, alias="failurePolicy")
    rerun_triggers: list[str] = Field(default_factory=list, alias="rerunTriggers")
    recommended_previous_agents: list[str] = Field(default_factory=list, alias="recommendedPreviousAgents")
    recommended_next_agents: list[str] = Field(default_factory=list, alias="recommendedNextAgents")
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActorContext(ContractModel):
    user_id: str = Field(..., alias="userId")
    tenant_id: str = Field(..., alias="tenantId")
    roles: list[str] = Field(default_factory=list)


class ProjectContext(ContractModel):
    project_id: str | None = Field(None, alias="projectId")
    asset_type: str | None = Field(None, alias="assetType")
    project_version: str | None = Field(None, alias="projectVersion")
    snapshot_id: str | None = Field(None, alias="snapshotId")


class ExecutionConstraints(ContractModel):
    data_classification: DataClassification = Field(..., alias="dataClassification")
    max_cost_usd: float | None = Field(None, ge=0, alias="maxCostUsd")
    timeout_seconds: int | None = Field(None, ge=1, alias="timeoutSeconds")
    preferred_brain_profile_id: str | None = Field(None, alias="preferredBrainProfileId")
    prohibited_providers: list[str] = Field(default_factory=list, alias="prohibitedProviders")
    local_only: bool = Field(False, alias="localOnly")
    deterministic_only: bool = Field(False, alias="deterministicOnly")


class SourceLocator(ContractModel):
    """Typed locator for a document page, Excel cell, API row, or Agent artifact."""

    source_name: str | None = Field(None, alias="sourceName")
    source_type: str | None = Field(None, alias="sourceType")
    evidence_id: str | None = Field(None, alias="evidenceId")
    source_url: str | None = Field(None, alias="sourceUrl")
    document_id: str | None = Field(None, alias="documentId")
    document_name: str | None = Field(None, alias="documentName")
    page_number: int | None = Field(None, ge=1, alias="pageNumber")
    sheet_name: str | None = Field(None, alias="sheetName")
    cell_reference: str | None = Field(None, alias="cellReference")
    api_endpoint: str | None = Field(None, alias="apiEndpoint")
    api_row_id: str | None = Field(None, alias="apiRowId")
    artifact_key: str | None = Field(None, alias="artifactKey")
    json_path: str | None = Field(None, alias="jsonPath")
    captured_at: datetime | None = Field(None, alias="capturedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_explicit(self) -> bool:
        return any(
            (
                self.evidence_id,
                self.source_url,
                self.document_id and self.page_number,
                self.sheet_name and self.cell_reference,
                self.api_endpoint and self.api_row_id,
                self.artifact_key,
            )
        )

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "SourceLocator":
        normalized = dict(value or {})
        aliases = {
            "source": "sourceName",
            "url": "sourceUrl",
            "page": "pageNumber",
            "pageNo": "pageNumber",
            "pageno": "pageNumber",
            "sheet": "sheetName",
            "cell": "cellReference",
            "range": "cellReference",
            "endpoint": "apiEndpoint",
            "rowId": "apiRowId",
            "artifact_path": "jsonPath",
        }
        for source_key, target_key in aliases.items():
            if target_key not in normalized and source_key in normalized:
                normalized[target_key] = normalized[source_key]
        allowed = set(cls.model_fields) | {field.alias for field in cls.model_fields.values() if field.alias}
        return cls.model_validate({key: item for key, item in normalized.items() if key in allowed})


class EvidenceReference(ContractModel):
    evidence_id: str = Field(..., alias="evidenceId")
    evidence_type: str = Field(..., alias="evidenceType")
    source_name: str = Field(..., alias="sourceName")
    source_locator: SourceLocator | None = Field(None, alias="sourceLocator")
    captured_at: datetime | None = Field(None, alias="capturedAt")
    checksum: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRequest(ContractModel):
    request_id: str = Field(..., alias="requestId")
    agent_id: str = Field(..., alias="agentId")
    agent_version: str | None = Field(None, alias="agentVersion")
    task_type: str = Field(..., alias="taskType")
    actor: ActorContext
    project: ProjectContext | None = None
    constraints: ExecutionConstraints
    input_schema_name: str = Field(..., alias="inputSchemaName")
    input_schema_version: str = Field(..., alias="inputSchemaVersion")
    payload: dict[str, Any]
    evidence: list[EvidenceReference] = Field(default_factory=list)
    upstream_results: list[str] = Field(default_factory=list, alias="upstreamResults")
    idempotency_key: str = Field(..., alias="idempotencyKey")
    requested_at: datetime = Field(..., alias="requestedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunBudget(ContractModel):
    max_cost_usd: float = Field(0, ge=0, alias="maxCostUsd")
    consumed_cost_usd: float = Field(0, ge=0, alias="consumedCostUsd")
    max_llm_calls: int = Field(0, ge=0, alias="maxLlmCalls")
    consumed_llm_calls: int = Field(0, ge=0, alias="consumedLlmCalls")
    max_tool_calls: int = Field(0, ge=0, alias="maxToolCalls")
    consumed_tool_calls: int = Field(0, ge=0, alias="consumedToolCalls")


class AgentExecutionContext(ContractModel):
    run_id: str = Field(..., alias="runId")
    correlation_id: str = Field(..., alias="correlationId")
    trace_id: str = Field(..., alias="traceId")
    actor: ActorContext
    project: ProjectContext | None = None
    constraints: ExecutionConstraints
    budget: RunBudget
    selected_brain_profile_id: str | None = Field(None, alias="selectedBrainProfileId")
    selected_provider: str | None = Field(None, alias="selectedProvider")
    selected_model: str | None = Field(None, alias="selectedModel")
    working_memory: dict[str, Any] = Field(default_factory=dict, alias="workingMemory")
    retrieved_memory_ids: list[str] = Field(default_factory=list, alias="retrievedMemoryIds")
    started_at: datetime = Field(..., alias="startedAt")
    deadline_at: datetime = Field(..., alias="deadlineAt")
    cancellation_requested: bool = Field(False, alias="cancellationRequested")

    _runtime_values: dict[str, Any] = PrivateAttr(default_factory=dict)

    def set_runtime_value(self, key: str, value: Any) -> None:
        """Attach an execution-only value that can never be serialized or persisted."""
        self._runtime_values[key] = value

    def get_runtime_value(self, key: str, default: Any = None) -> Any:
        return self._runtime_values.get(key, default)

    def is_cancellation_requested(self) -> bool:
        """Check the persisted cancellation flag without serializing the probe."""
        if self.cancellation_requested:
            return True
        probe = self.get_runtime_value("cancellation_probe")
        if callable(probe):
            try:
                return bool(probe())
            except Exception:
                return False
        return False


class CapabilityIssue(ContractModel):
    code: str
    message: str
    blocking: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilityCheck(ContractModel):
    can_handle: bool = Field(..., alias="canHandle")
    issues: list[CapabilityIssue] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list, alias="requiredCapabilities")
    missing_tools: list[str] = Field(default_factory=list, alias="missingTools")
    missing_evidence: list[str] = Field(default_factory=list, alias="missingEvidence")


class ExecutionStep(ContractModel):
    step_id: str = Field(..., alias="stepId")
    step_type: str = Field(..., alias="stepType")
    name: str
    description: str
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    tool_id: str | None = Field(None, alias="toolId")
    requires_brain: bool = Field(False, alias="requiresBrain")
    required_capabilities: list[str] = Field(default_factory=list, alias="requiredCapabilities")
    timeout_seconds: int = Field(30, ge=1, alias="timeoutSeconds")
    retryable: bool = True


class ExecutionPlan(ContractModel):
    plan_id: str = Field(..., alias="planId")
    agent_id: str = Field(..., alias="agentId")
    steps: list[ExecutionStep]
    expected_output_schema: str = Field(..., alias="expectedOutputSchema")
    estimated_cost_usd: float = Field(0, ge=0, alias="estimatedCostUsd")
    estimated_tool_calls: int = Field(0, ge=0, alias="estimatedToolCalls")
    estimated_llm_calls: int = Field(0, ge=0, alias="estimatedLlmCalls")
    requires_human_review: bool = Field(False, alias="requiresHumanReview")
    created_at: datetime = Field(..., alias="createdAt")


class Finding(ContractModel):
    finding_id: str = Field(..., alias="findingId")
    category: str
    title: str
    description: str
    severity: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")
    source_locators: list[SourceLocator] = Field(default_factory=list, alias="sourceLocators")
    confidence: float = Field(..., ge=0, le=1)


class Assumption(ContractModel):
    assumption_id: str = Field(..., alias="assumptionId")
    description: str
    reason: str
    materiality: str
    requires_human_confirmation: bool = Field(False, alias="requiresHumanConfirmation")


class Blocker(ContractModel):
    code: str
    severity: str
    message: str
    required_fields: list[str] = Field(default_factory=list, alias="requiredFields")
    resolution_hint: str | None = Field(None, alias="resolutionHint")


class BrainUsage(ContractModel):
    brain_profile_id: str | None = Field(None, alias="brainProfileId")
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = Field(None, alias="promptVersion")
    input_tokens: int | None = Field(None, ge=0, alias="inputTokens")
    output_tokens: int | None = Field(None, ge=0, alias="outputTokens")
    estimated_cost_usd: float | None = Field(None, ge=0, alias="estimatedCostUsd")
    latency_ms: int | None = Field(None, ge=0, alias="latencyMs")


class QualityMetrics(ContractModel):
    schema_valid: bool = Field(..., alias="schemaValid")
    evidence_coverage: float = Field(..., ge=0, le=1, alias="evidenceCoverage")
    consistency_score: float = Field(..., ge=0, le=1, alias="consistencyScore")
    completeness_score: float = Field(..., ge=0, le=1, alias="completenessScore")
    deterministic_checks_passed: bool = Field(..., alias="deterministicChecksPassed")
    unsupported_claim_count: int = Field(0, ge=0, alias="unsupportedClaimCount")

    def deterministic_confidence(self) -> float:
        score = (
            self.evidence_coverage * 0.40
            + self.consistency_score * 0.30
            + self.completeness_score * 0.30
        )
        if not self.schema_valid or not self.deterministic_checks_passed:
            score *= 0.5
        score -= min(self.unsupported_claim_count * 0.05, 0.35)
        return round(max(0.0, min(1.0, score)), 4)


class AgentResult(ContractModel):
    run_id: str = Field(..., alias="runId")
    request_id: str = Field(..., alias="requestId")
    agent_id: str = Field(..., alias="agentId")
    agent_version: str = Field(..., alias="agentVersion")
    status: AgentRunStatus
    output_schema_name: str = Field(..., alias="outputSchemaName")
    output_schema_version: str = Field(..., alias="outputSchemaVersion")
    output: dict[str, Any] | None = None
    findings: list[Finding] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")
    source_locators: list[SourceLocator] = Field(default_factory=list, alias="sourceLocators")
    artifact_ids: list[str] = Field(default_factory=list, alias="artifactIds")
    confidence: float = Field(..., ge=0, le=1)
    recommended_next_agents: list[str] = Field(default_factory=list, alias="recommendedNextAgents")
    rerun_recommendations: list[str] = Field(default_factory=list, alias="rerunRecommendations")
    brain_usage: BrainUsage | None = Field(None, alias="brainUsage")
    quality_metrics: QualityMetrics = Field(..., alias="qualityMetrics")
    requires_human_review: bool = Field(False, alias="requiresHumanReview")
    review_reasons: list[str] = Field(default_factory=list, alias="reviewReasons")
    created_at: datetime = Field(..., alias="createdAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_status_invariants(self) -> "AgentResult":
        if any(item.severity == "blocking" for item in self.blockers):
            self.status = AgentRunStatus.BLOCKED
        self.confidence = self.quality_metrics.deterministic_confidence()
        return self


class AgentRunError(ContractModel):
    code: str
    message: str
    retryable: bool = False
    exception_type: str | None = Field(None, alias="exceptionType")
    occurred_at: datetime = Field(..., alias="occurredAt")
    details: dict[str, Any] = Field(default_factory=dict)


class AgentRunRecord(ContractModel):
    schema_version: str = Field("realytics-agent-run-v1", alias="schemaVersion")
    run_id: str = Field(..., alias="runId")
    request_id: str = Field(..., alias="requestId")
    correlation_id: str = Field(..., alias="correlationId")
    trace_id: str = Field(..., alias="traceId")
    tenant_id: str = Field(..., alias="tenantId")
    user_id: str = Field(..., alias="userId")
    project_id: str | None = Field(None, alias="projectId")
    agent_id: str = Field(..., alias="agentId")
    agent_version: str = Field(..., alias="agentVersion")
    task_type: str = Field(..., alias="taskType")
    idempotency_key: str = Field(..., alias="idempotencyKey")
    input_hash: str = Field(..., alias="inputHash")
    status: AgentRunStatus = AgentRunStatus.RECEIVED
    attempt_count: int = Field(0, ge=0, alias="attemptCount")
    max_attempts: int = Field(1, ge=1, alias="maxAttempts")
    cancellation_requested: bool = Field(False, alias="cancellationRequested")
    cancellation_reason: str | None = Field(None, alias="cancellationReason")
    lease_owner: str | None = Field(None, alias="leaseOwner")
    lease_expires_at: datetime | None = Field(None, alias="leaseExpiresAt")
    heartbeat_at: datetime | None = Field(None, alias="heartbeatAt")
    deadline_at: datetime | None = Field(None, alias="deadlineAt")
    budget: RunBudget = Field(default_factory=RunBudget)
    retry_of_run_id: str | None = Field(None, alias="retryOfRunId")
    state_version: int = Field(1, ge=1, alias="stateVersion")
    selected_brain_profile_id: str | None = Field(None, alias="selectedBrainProfileId")
    selected_provider: str | None = Field(None, alias="selectedProvider")
    selected_model: str | None = Field(None, alias="selectedModel")
    request_summary: dict[str, Any] = Field(default_factory=dict, alias="requestSummary")
    result_envelope: AgentResult | None = Field(None, alias="resultEnvelope")
    last_error: AgentRunError | None = Field(None, alias="lastError")
    error_history: list[AgentRunError] = Field(default_factory=list, alias="errorHistory")
    artifact_ids: list[str] = Field(default_factory=list, alias="artifactIds")
    brain_usage_event_ids: list[str] = Field(default_factory=list, alias="brainUsageEventIds")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    started_at: datetime | None = Field(None, alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunStepRecord(ContractModel):
    schema_version: str = Field("realytics-agent-run-step-v1", alias="schemaVersion")
    step_id: str = Field(..., alias="stepId")
    run_id: str = Field(..., alias="runId")
    project_id: str | None = Field(None, alias="projectId")
    sequence_no: int = Field(..., ge=1, alias="sequenceNo")
    stage: AgentRunStatus
    step_type: str = Field(..., alias="stepType")
    name: str
    status: AgentRunStepStatus
    attempt_no: int | None = Field(None, ge=1, alias="attemptNo")
    input_hash: str | None = Field(None, alias="inputHash")
    output_hash: str | None = Field(None, alias="outputHash")
    error: AgentRunError | None = None
    started_at: datetime = Field(..., alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrainUsageEvent(ContractModel):
    schema_version: str = Field("realytics-brain-usage-event-v1", alias="schemaVersion")
    event_id: str = Field(..., alias="eventId")
    run_id: str = Field(..., alias="runId")
    step_id: str | None = Field(None, alias="stepId")
    tenant_id: str = Field(..., alias="tenantId")
    project_id: str | None = Field(None, alias="projectId")
    agent_id: str = Field(..., alias="agentId")
    agent_version: str = Field(..., alias="agentVersion")
    brain_profile_id: str | None = Field(None, alias="brainProfileId")
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = Field(None, alias="promptVersion")
    status: BrainUsageEventStatus = BrainUsageEventStatus.COMPLETED
    input_tokens: int | None = Field(None, ge=0, alias="inputTokens")
    output_tokens: int | None = Field(None, ge=0, alias="outputTokens")
    estimated_cost_usd: float | None = Field(None, ge=0, alias="estimatedCostUsd")
    latency_ms: int | None = Field(None, ge=0, alias="latencyMs")
    fallback_reason: str | None = Field(None, alias="fallbackReason")
    occurred_at: datetime = Field(..., alias="occurredAt")
    metadata: dict[str, Any] = Field(default_factory=dict)


class DurableAgentExecution(ContractModel):
    run: AgentRunRecord
    result: AgentResult
    replayed: bool = False
    step_ids: list[str] = Field(default_factory=list, alias="stepIds")
    brain_usage_event_ids: list[str] = Field(default_factory=list, alias="brainUsageEventIds")


class ValidationResult(ContractModel):
    valid: bool
    issues: list[CapabilityIssue] = Field(default_factory=list)
    requires_human_review: bool = Field(False, alias="requiresHumanReview")


class BrainCapabilities(ContractModel):
    structured_output: bool = Field(False, alias="structuredOutput")
    tool_calling: bool = Field(False, alias="toolCalling")
    long_context: bool = Field(False, alias="longContext")
    vision: bool = False
    code_generation: bool = Field(False, alias="codeGeneration")
    korean_reasoning_level: str = Field("unknown", alias="koreanReasoningLevel")

    def supports(self, name: str, minimum_level: str | None = None) -> bool:
        normalized = name.strip().lower()
        if normalized == "korean_reasoning":
            levels = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
            return levels.get(self.korean_reasoning_level, 0) >= levels.get(minimum_level or "low", 1)
        mapping = {
            "structured_output": self.structured_output,
            "tool_calling": self.tool_calling,
            "long_context": self.long_context,
            "vision": self.vision,
            "code_generation": self.code_generation,
        }
        return bool(mapping.get(normalized, False))


class BrainLimits(ContractModel):
    max_context_tokens: int = Field(..., ge=1, alias="maxContextTokens")
    max_output_tokens: int = Field(..., ge=1, alias="maxOutputTokens")
    max_cost_per_run_usd: float = Field(..., ge=0, alias="maxCostPerRunUsd")
    requests_per_minute: int | None = Field(None, ge=1, alias="requestsPerMinute")


class BrainPrivacyPolicy(ContractModel):
    allowed_data_classifications: list[DataClassification] = Field(..., alias="allowedDataClassifications")
    prohibited_data_types: list[str] = Field(default_factory=list, alias="prohibitedDataTypes")
    data_retention_allowed: bool = Field(False, alias="dataRetentionAllowed")
    local_execution: bool = Field(False, alias="localExecution")


class BrainProfile(ContractModel):
    brain_profile_id: str = Field(..., alias="brainProfileId")
    provider: str
    model: str
    enabled: bool = True
    capabilities: BrainCapabilities
    limits: BrainLimits
    privacy: BrainPrivacyPolicy
    secret_ref: str | None = Field(None, alias="secretRef")
    preferred_tasks: list[str] = Field(default_factory=list, alias="preferredTasks")
    prohibited_tasks: list[str] = Field(default_factory=list, alias="prohibitedTasks")
    quality_tier: str = Field("standard", alias="qualityTier")
    cost_tier: str = Field("standard", alias="costTier")
    latency_tier: str = Field("standard", alias="latencyTier")
    fallback_profile_ids: list[str] = Field(default_factory=list, alias="fallbackProfileIds")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_inline_secrets(self) -> "BrainProfile":
        prohibited = {"api_key", "apikey", "apiKey", "token", "password", "secret"}
        if _contains_prohibited_key(self.metadata, prohibited):
            raise ValueError("BrainProfile metadata must contain a secretRef, never an inline secret")
        return self


class BrainRequest(ContractModel):
    system_instructions: str = Field(..., alias="systemInstructions")
    messages: list[dict[str, Any]]
    output_schema: dict[str, Any] | None = Field(None, alias="outputSchema")
    tools: list[dict[str, Any]] = Field(default_factory=list)
    max_output_tokens: int | None = Field(None, ge=1, alias="maxOutputTokens")
    temperature: float | None = Field(None, ge=0, le=2)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrainResponse(ContractModel):
    text: str | None = None
    structured_output: dict[str, Any] | None = Field(None, alias="structuredOutput")
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, alias="toolCalls")
    input_tokens: int | None = Field(None, ge=0, alias="inputTokens")
    output_tokens: int | None = Field(None, ge=0, alias="outputTokens")
    estimated_cost_usd: float | None = Field(None, ge=0, alias="estimatedCostUsd")
    latency_ms: int | None = Field(None, ge=0, alias="latencyMs")
    provider_request_id: str | None = Field(None, alias="providerRequestId")
    finish_reason: str | None = Field(None, alias="finishReason")
    raw_metadata: dict[str, Any] = Field(default_factory=dict, alias="rawMetadata")


class BrainRoutingRequest(ContractModel):
    task_type: str = Field(..., alias="taskType")
    risk_level: RiskLevel = Field(..., alias="riskLevel")
    data_classification: DataClassification = Field(..., alias="dataClassification")
    required_capabilities: list[CapabilityRequirement] = Field(default_factory=list, alias="requiredCapabilities")
    context_token_estimate: int = Field(0, ge=0, alias="contextTokenEstimate")
    estimated_output_tokens: int = Field(0, ge=0, alias="estimatedOutputTokens")
    tenant_id: str = Field(..., alias="tenantId")
    user_id: str = Field(..., alias="userId")
    project_id: str | None = Field(None, alias="projectId")
    run_id: str | None = Field(None, alias="runId")
    request_id: str | None = Field(None, alias="requestId")
    policy_id: str | None = Field(None, alias="policyId")
    policy_version: int | None = Field(None, ge=1, alias="policyVersion")
    preferred_profile_id: str | None = Field(None, alias="preferredProfileId")
    prohibited_providers: list[str] = Field(default_factory=list, alias="prohibitedProviders")
    max_cost_usd: float = Field(0, ge=0, alias="maxCostUsd")
    local_only: bool = Field(False, alias="localOnly")


class BrainRoutingCandidate(ContractModel):
    brain_profile_id: str = Field(..., alias="brainProfileId")
    eligible: bool
    rejection_reasons: list[str] = Field(default_factory=list, alias="rejectionReasons")
    score: float | None = None
    score_components: dict[str, float] = Field(default_factory=dict, alias="scoreComponents")
    estimated_cost_usd: float | None = Field(None, alias="estimatedCostUsd")


class BrainRoutingResult(ContractModel):
    routing_event_id: str | None = Field(None, alias="routingEventId")
    selected_profile_id: str | None = Field(None, alias="selectedProfileId")
    selected_provider: str | None = Field(None, alias="selectedProvider")
    selected_model: str | None = Field(None, alias="selectedModel")
    candidates: list[BrainRoutingCandidate]
    routing_policy_version: str = Field(..., alias="routingPolicyVersion")
    fallback_profile_ids: list[str] = Field(default_factory=list, alias="fallbackProfileIds")
    selected_at: datetime = Field(..., alias="selectedAt")
    blocked: bool = False
    blocked_reason: str | None = Field(None, alias="blockedReason")


def _contains_prohibited_key(value: Any, prohibited: set[str]) -> bool:
    if isinstance(value, dict):
        return any(key in prohibited or _contains_prohibited_key(item, prohibited) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_prohibited_key(item, prohibited) for item in value)
    return False
