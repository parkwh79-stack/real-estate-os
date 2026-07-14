from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from agent_platform.contracts import (
    AgentExecutionContext,
    AgentManifest,
    AgentRequest,
    AgentResult,
    AgentRunStatus,
    CapabilityCheck,
    CapabilityIssue,
    EvidenceRequirement,
    ExecutionPlan,
    ExecutionStep,
    Finding,
    QualityMetrics,
    ValidationResult,
)


LegacyHandler = Callable[[AgentRequest, AgentExecutionContext, ExecutionPlan], Any | Awaitable[Any]]
ResultMapper = Callable[[Any, AgentRequest, AgentExecutionContext, AgentManifest], AgentResult]


class LegacyAgentAdapter:
    """Migration adapter that gives an existing Agent the common lifecycle contract.

    This is a controlled migration path, not a claim that the wrapped Agent has
    already adopted AgentRun persistence, cancellation, retry, or ToolRegistry.
    """

    def __init__(
        self,
        *,
        manifest: AgentManifest,
        handler: LegacyHandler,
        result_mapper: ResultMapper | None = None,
        available_tools: set[str] | None = None,
    ):
        self.manifest = manifest
        self._handler = handler
        self._result_mapper = result_mapper or _default_result_mapper
        self._available_tools = set(available_tools or set())

    def can_handle(self, request: AgentRequest) -> CapabilityCheck:
        issues: list[CapabilityIssue] = []
        if request.agent_id != self.manifest.agent_id:
            issues.append(CapabilityIssue(code="AGENT_ID_MISMATCH", message="Request Agent ID does not match manifest."))
        if request.agent_version and request.agent_version != self.manifest.version:
            issues.append(CapabilityIssue(code="AGENT_VERSION_MISMATCH", message="Requested Agent version is not registered."))
        if request.task_type not in self.manifest.supported_task_types:
            issues.append(CapabilityIssue(code="TASK_NOT_SUPPORTED", message=f"Unsupported task type: {request.task_type}"))
        if request.constraints.data_classification not in self.manifest.supported_data_classifications:
            issues.append(CapabilityIssue(code="DATA_CLASSIFICATION_NOT_ALLOWED", message="Agent cannot process this data classification."))
        if request.input_schema_name != self.manifest.input_schema_name:
            issues.append(CapabilityIssue(code="INPUT_SCHEMA_MISMATCH", message="Input schema does not match Agent manifest."))
        missing_tools = sorted(set(self.manifest.required_tools) - self._available_tools)
        if missing_tools:
            issues.append(
                CapabilityIssue(
                    code="REQUIRED_TOOL_MISSING",
                    message="One or more required tools are not registered for this Agent adapter.",
                    details={"toolIds": missing_tools},
                )
            )
        missing_evidence: list[str] = []
        requirement = self.manifest.execution_policy.evidence_requirement
        if requirement in {EvidenceRequirement.REQUIRED, EvidenceRequirement.STRICT} and not request.evidence:
            missing_evidence.append("input_evidence")
            issues.append(CapabilityIssue(code="EVIDENCE_REQUIRED", message="Agent requires evidence-backed input."))
        if requirement == EvidenceRequirement.STRICT and request.evidence:
            weak = [item.evidence_id for item in request.evidence if not item.source_locator or not item.source_locator.is_explicit]
            if weak:
                missing_evidence.extend(weak)
                issues.append(
                    CapabilityIssue(
                        code="SOURCE_LOCATOR_REQUIRED",
                        message="Strict evidence requires an explicit document page, Excel cell, API row, or artifact key.",
                        details={"evidenceIds": weak},
                    )
                )
        return CapabilityCheck(
            can_handle=not any(item.blocking for item in issues),
            issues=issues,
            required_capabilities=[item.name for item in self.manifest.required_capabilities if item.required],
            missing_tools=missing_tools,
            missing_evidence=missing_evidence,
        )

    def prepare(self, request: AgentRequest, context: AgentExecutionContext) -> ExecutionPlan:
        check = self.can_handle(request)
        if not check.can_handle:
            raise ValueError("Agent capability check failed: " + ", ".join(item.code for item in check.issues))
        policy = self.manifest.execution_policy
        return ExecutionPlan(
            plan_id=f"plan:{self.manifest.agent_id}:{uuid4()}",
            agent_id=self.manifest.agent_id,
            steps=[
                ExecutionStep(
                    step_id="legacy-agent-execution",
                    step_type="legacy_adapter",
                    name=self.manifest.display_name,
                    description="Execute the existing Agent behind the common contract.",
                    requires_brain=policy.llm_required,
                    required_capabilities=[item.name for item in self.manifest.required_capabilities if item.required],
                    timeout_seconds=min(policy.max_execution_seconds, context.constraints.timeout_seconds or policy.max_execution_seconds),
                    retryable=bool(self.manifest.failure_policy.retryable_error_codes),
                )
            ],
            expected_output_schema=f"{self.manifest.output_schema_name}@{self.manifest.output_schema_version}",
            estimated_cost_usd=0,
            estimated_tool_calls=0,
            estimated_llm_calls=0 if policy.mode.value == "deterministic_only" else policy.max_llm_calls,
            requires_human_review=policy.human_review.value == "required",
            created_at=datetime.now(timezone.utc),
        )

    def execute_sync(
        self,
        request: AgentRequest,
        context: AgentExecutionContext,
        plan: ExecutionPlan,
    ) -> AgentResult:
        if context.is_cancellation_requested():
            return _cancelled_result(request, context, self.manifest)
        raw = self._handler(request, context, plan)
        if inspect.isawaitable(raw):
            raise RuntimeError("Async legacy handler must be invoked through execute(), not execute_sync().")
        return self._result_mapper(raw, request, context, self.manifest)

    async def execute(
        self,
        request: AgentRequest,
        context: AgentExecutionContext,
        plan: ExecutionPlan,
    ) -> AgentResult:
        if context.is_cancellation_requested():
            return _cancelled_result(request, context, self.manifest)
        raw = self._handler(request, context, plan)
        if inspect.isawaitable(raw):
            raw = await raw
        return self._result_mapper(raw, request, context, self.manifest)

    def validate(
        self,
        request: AgentRequest,
        result: AgentResult,
        context: AgentExecutionContext,
    ) -> ValidationResult:
        issues: list[CapabilityIssue] = []
        if result.agent_id != self.manifest.agent_id or result.agent_version != self.manifest.version:
            issues.append(CapabilityIssue(code="RESULT_IDENTITY_MISMATCH", message="Result identity does not match Agent manifest."))
        if result.output_schema_name != self.manifest.output_schema_name:
            issues.append(CapabilityIssue(code="OUTPUT_SCHEMA_MISMATCH", message="Result schema does not match Agent manifest."))
        if result.output is None and result.status not in {AgentRunStatus.BLOCKED, AgentRunStatus.CANCELLED}:
            issues.append(CapabilityIssue(code="OUTPUT_MISSING", message="Completed Agent result has no typed output."))
        if result.status == AgentRunStatus.COMPLETED and result.requires_human_review:
            issues.append(CapabilityIssue(code="HUMAN_REVIEW_STATUS_MISMATCH", message="Human-review result cannot be marked fully completed."))
        return ValidationResult(
            valid=not any(item.blocking for item in issues),
            issues=issues,
            requires_human_review=result.requires_human_review,
        )


def _default_result_mapper(
    raw: Any,
    request: AgentRequest,
    context: AgentExecutionContext,
    manifest: AgentManifest,
) -> AgentResult:
    output = raw if isinstance(raw, dict) else {"value": raw}
    explicit_locators = [item.source_locator for item in request.evidence if item.source_locator and item.source_locator.is_explicit]
    evidence_coverage = len(explicit_locators) / len(request.evidence) if request.evidence else 0.0
    quality = QualityMetrics(
        schema_valid=isinstance(output, dict),
        evidence_coverage=evidence_coverage,
        consistency_score=1.0,
        completeness_score=1.0 if output else 0.0,
        deterministic_checks_passed=True,
    )
    return AgentResult(
        run_id=context.run_id,
        request_id=request.request_id,
        agent_id=manifest.agent_id,
        agent_version=manifest.version,
        status=AgentRunStatus.COMPLETED,
        output_schema_name=manifest.output_schema_name,
        output_schema_version=manifest.output_schema_version,
        output=output,
        evidence_ids=[item.evidence_id for item in request.evidence],
        source_locators=[item for item in explicit_locators if item is not None],
        confidence=quality.deterministic_confidence(),
        quality_metrics=quality,
        created_at=datetime.now(timezone.utc),
        metadata={"adapter": "LegacyAgentAdapter", "implementationLevel": manifest.implementation_level.value},
    )


def _cancelled_result(request: AgentRequest, context: AgentExecutionContext, manifest: AgentManifest) -> AgentResult:
    quality = QualityMetrics(
        schema_valid=True,
        evidence_coverage=0,
        consistency_score=1,
        completeness_score=0,
        deterministic_checks_passed=False,
    )
    return AgentResult(
        run_id=context.run_id,
        request_id=request.request_id,
        agent_id=manifest.agent_id,
        agent_version=manifest.version,
        status=AgentRunStatus.CANCELLED,
        output_schema_name=manifest.output_schema_name,
        output_schema_version=manifest.output_schema_version,
        confidence=0,
        quality_metrics=quality,
        requires_human_review=False,
        created_at=datetime.now(timezone.utc),
    )
