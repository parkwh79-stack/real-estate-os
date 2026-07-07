from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


PF_REVIEW_WORKFLOW = "pf-review"
AGENT_HANDOFF_SCHEMA_VERSION = "realytics-agent-handoff-v1"
AGENT_PROGRESS_SCHEMA_VERSION = "realytics-agent-progress-v1"
CHIEF_DEAL_ORCHESTRATION_SCHEMA_VERSION = "realytics-chief-deal-orchestration-v1"
POD_AI_REVIEW_SCHEMA_VERSION = "realytics-pod-ai-review-v1"

PF_REVIEW_STEPS = [
    ("project_load", "Project data load"),
    ("term_annotation", "Controlled-vocabulary term annotation"),
    ("land_agent_review", "Land acquisition and evidence review"),
    ("development_feasibility_analysis", "Developer feasibility analysis"),
    ("feasibility_calculation", "Feasibility calculation"),
    ("pf_structure_calculation", "PF structure calculation"),
    ("return_metrics", "Return metrics (IRR/Equity/Payback/ICR)"),
    ("stress_test", "4-stage stress test"),
    ("credit_enhancement_review", "Credit enhancement mapping"),
    ("cash_sweep_simulation", "Cash sweep / reserve simulation"),
    ("market_evidence_reconciliation", "Market evidence reconciliation"),
    ("marketability_analysis", "Marketability analysis"),
    ("data_quality_review", "Data quality review"),
    ("cross_agent_reconciliation", "Cross-agent reconciliation gate"),
    ("consensus_resolution", "Iterative cross-agent consensus"),
    ("legal_permit_review", "Legal and permit pre-review"),
    ("risk_assessment", "Risk assessment"),
    ("risk_register_generation", "Risk register generation"),
    ("credit_review_decision", "Credit review decision"),
    ("scenario_restructuring_plan", "Scenario restructuring plan"),
    ("deal_optimization", "Deal optimization (협의 권고안)"),
    ("developability_score", "Developability score"),
    ("phase11_report_generation", "Phase 11 report generation"),
    ("evidence_graph_generation", "Evidence graph generation"),
    ("quality_check", "Report completeness quality check"),
    ("approval_readiness_gate", "Approval readiness gate"),
    ("credit_document_generation", "Term Sheet and IC Memo generation"),
]

TERMINAL_RUN_STATUSES = {"completed", "completed_with_warnings", "failed", "approved"}


class AgentHandoffEnvelope(BaseModel):
    """Traceable communication artifact from one agent to another."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: str = Field(
        AGENT_HANDOFF_SCHEMA_VERSION,
        alias="schemaVersion",
    )
    handoff_id: str = Field(..., alias="handoffId")
    workflow: str = PF_REVIEW_WORKFLOW
    run_id: Optional[str] = Field(None, alias="runId")
    handoff_type: str = Field(..., alias="handoffType")
    from_agent: str = Field(..., alias="fromAgent")
    to_agent: str = Field(..., alias="toAgent")
    status: Literal["created", "accepted", "blocked", "needs_review", "superseded"] = "created"
    payload_schema: Optional[str] = Field(None, alias="payloadSchema")
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence_ids: List[str] = Field(default_factory=list, alias="evidenceIds")
    confidence: Optional[float] = Field(None, ge=0, le=1)
    blocking_issues: List[str] = Field(default_factory=list, alias="blockingIssues")
    assumptions: List[str] = Field(default_factory=list)
    data_limitations: List[str] = Field(default_factory=list, alias="dataLimitations")
    requires_human_review: bool = Field(False, alias="requiresHumanReview")
    review_status: Literal["not_required", "pending", "approved", "rejected", "needs_revision"] = Field(
        "not_required",
        alias="reviewStatus",
    )
    reviewed_at: Optional[str] = Field(None, alias="reviewedAt")
    reviewed_by: Optional[str] = Field(None, alias="reviewedBy")
    review_comment: Optional[str] = Field(None, alias="reviewComment")
    review_history: List[Dict[str, Any]] = Field(default_factory=list, alias="reviewHistory")
    created_at: str = Field(..., alias="createdAt")
    progress_impact: Dict[str, Any] = Field(default_factory=dict, alias="progressImpact")


class AgentProgress(BaseModel):
    """Per-agent execution state for an orchestration run."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: str = Field(
        AGENT_PROGRESS_SCHEMA_VERSION,
        alias="schemaVersion",
    )
    agent_key: str = Field(..., alias="agentKey")
    agent_name: str = Field(..., alias="agentName")
    status: Literal[
        "pending",
        "running",
        "completed",
        "completed_with_warnings",
        "blocked",
        "failed",
        "skipped",
    ] = "pending"
    progress_percent: int = Field(0, ge=0, le=100, alias="progressPercent")
    completed_tasks: List[str] = Field(default_factory=list, alias="completedTasks")
    pending_tasks: List[str] = Field(default_factory=list, alias="pendingTasks")
    blocked_tasks: List[str] = Field(default_factory=list, alias="blockedTasks")
    depends_on: List[str] = Field(default_factory=list, alias="dependsOn")
    downstream_dependencies: List[str] = Field(default_factory=list, alias="downstreamDependencies")
    last_artifact_key: Optional[str] = Field(None, alias="lastArtifactKey")
    next_action: Optional[str] = Field(None, alias="nextAction")
    review_required: bool = Field(False, alias="reviewRequired")
    handoff_ids: List[str] = Field(default_factory=list, alias="handoffIds")
    updated_at: str = Field(..., alias="updatedAt")


class PodAIReviewFinding(BaseModel):
    """Evidence-aware reviewer finding attached to a Chief Pod artifact."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    finding_id: str = Field(..., alias="findingId")
    category: Literal[
        "missing_evidence",
        "source_locator_gap",
        "data_quality",
        "recalculation_needed",
        "risk_note",
        "handoff",
        "guardrail",
    ]
    severity: Literal["info", "medium", "high", "blocking"] = "medium"
    message: str
    evidence_ids: List[str] = Field(default_factory=list, alias="evidenceIds")
    source_locators: List[Dict[str, Any]] = Field(default_factory=list, alias="sourceLocators")
    recommended_action: Optional[str] = Field(None, alias="recommendedAction")


class PodAIReviewHandoff(BaseModel):
    """Traceable handoff suggested by a Pod AI reviewer."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    handoff_id: str = Field(..., alias="handoffId")
    from_agent: str = Field(..., alias="fromAgent")
    to_agent: str = Field(..., alias="toAgent")
    handoff_type: Literal["blocker", "work_order", "recalculation", "report_note", "review_request"] = Field(
        ...,
        alias="handoffType",
    )
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence_ids: List[str] = Field(default_factory=list, alias="evidenceIds")
    confidence: Optional[float] = Field(None, ge=0, le=1)


class PodAIReview(BaseModel):
    """Persisted evidence-aware AI reviewer output for a Chief Orchestrator Pod.

    The reviewer may challenge, explain, or request evidence, but it must not
    create report-grade financial calculations or source facts.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: str = Field(POD_AI_REVIEW_SCHEMA_VERSION, alias="schemaVersion")
    review_id: str = Field(..., alias="reviewId")
    project_id: str = Field(..., alias="projectId")
    run_id: Optional[str] = Field(None, alias="runId")
    pod_key: str = Field(..., alias="podKey")
    source_artifact_key: Optional[str] = Field(None, alias="sourceArtifactKey")
    source_artifact_keys: List[str] = Field(default_factory=list, alias="sourceArtifactKeys")
    agent_id: str = Field(..., alias="agentId")
    review_mode: Literal["llm_evidence_aware_review", "deterministic_fallback_review"] = Field(
        ...,
        alias="reviewMode",
    )
    review_status: Literal["pass", "warning", "blocking", "needs_human_review"] = Field(
        "warning",
        alias="reviewStatus",
    )
    confidence: Optional[float] = Field(None, ge=0, le=1)
    findings: List[PodAIReviewFinding] = Field(default_factory=list)
    handoffs: List[PodAIReviewHandoff] = Field(default_factory=list)
    report_fragments: Dict[str, str] = Field(default_factory=dict, alias="reportFragments")
    input_evidence_ids: List[str] = Field(default_factory=list, alias="inputEvidenceIds")
    input_assumption_ids: List[str] = Field(default_factory=list, alias="inputAssumptionIds")
    source_locators: List[Dict[str, Any]] = Field(default_factory=list, alias="sourceLocators")
    guardrails: List[str] = Field(default_factory=list)
    ai_brain_used: Dict[str, Any] = Field(default_factory=dict, alias="aiBrainUsed")
    ai_brain_used_event_id: Optional[str] = Field(None, alias="aiBrainUsedEventId")
    created_at: str = Field(..., alias="createdAt")
    updated_at: str = Field(..., alias="updatedAt")


class DealStateValue(BaseModel):
    """A material value stored in the Shared Deal State Graph."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    key: str
    value: Any
    value_type: Literal["source", "assumption", "calculated", "estimated"] = Field(..., alias="valueType")
    unit: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0, le=1)
    evidence_ids: List[str] = Field(default_factory=list, alias="evidenceIds")
    source_locator: Optional[Dict[str, Any]] = Field(None, alias="sourceLocator")
    review_status: Literal["unreviewed", "approved", "needs_review", "rejected", "superseded"] = Field(
        "unreviewed",
        alias="reviewStatus",
    )


class MissingInputRegisterItem(BaseModel):
    """A missing input required before a pod or decision gate can complete."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    item_id: str = Field(..., alias="itemId")
    category: Literal["land", "legal", "market", "product", "cost", "schedule", "feasibility", "cash_flow", "pf", "policy", "report"]
    description: str
    required_for: List[str] = Field(default_factory=list, alias="requiredFor")
    severity: Literal["info", "medium", "high", "blocking"] = "medium"
    acceptable_sources: List[str] = Field(default_factory=list, alias="acceptableSources")
    status: Literal["open", "requested", "received", "waived", "resolved"] = "open"


class SharedDealStateGraph(BaseModel):
    """Canonical deal-room state shared by all agent pods."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: str = Field(CHIEF_DEAL_ORCHESTRATION_SCHEMA_VERSION, alias="schemaVersion")
    project_id: Optional[str] = Field(None, alias="projectId")
    project_memory: Dict[str, Any] = Field(default_factory=dict, alias="projectMemory")
    evidence_vault: List[Dict[str, Any]] = Field(default_factory=list, alias="evidenceVault")
    assumption_book: List[DealStateValue] = Field(default_factory=list, alias="assumptionBook")
    metrics: List[DealStateValue] = Field(default_factory=list)
    risks: List[Dict[str, Any]] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    missing_input_register: List[MissingInputRegisterItem] = Field(default_factory=list, alias="missingInputRegister")


class AgentPodPlan(BaseModel):
    """A parallel pod plan generated by the Chief Deal Orchestrator."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    pod_key: Literal[
        "land_legal",
        "market_product",
        "cost_schedule",
        "feasibility_cashflow",
        "pf_credit",
        "public_policy",
    ] = Field(..., alias="podKey")
    name: str
    objective: str
    agents: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    can_run_parallel: bool = Field(True, alias="canRunParallel")
    required_inputs: List[str] = Field(default_factory=list, alias="requiredInputs")
    expected_outputs: List[str] = Field(default_factory=list, alias="expectedOutputs")


class AgentPodRun(BaseModel):
    """Runtime status for one pod execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    pod_key: str = Field(..., alias="podKey")
    status: Literal["planned", "running", "completed", "blocked", "failed", "needs_recalculation"] = "planned"
    progress_percent: int = Field(0, ge=0, le=100, alias="progressPercent")
    artifact_keys: List[str] = Field(default_factory=list, alias="artifactKeys")
    pod_ai_review_ids: List[str] = Field(default_factory=list, alias="podAiReviewIds")
    handoff_ids: List[str] = Field(default_factory=list, alias="handoffIds")
    recalculation_events: List[Dict[str, Any]] = Field(default_factory=list, alias="recalculationEvents")


class RiskCommitteeOpinion(BaseModel):
    """One AI Risk Committee member's structured opinion."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    reviewer_role: Literal[
        "devils_advocate",
        "credit_reviewer",
        "legal_compliance_reviewer",
        "market_stress_tester",
        "construction_risk_reviewer",
        "lender_persona_reviewer",
        "ic_judge",
    ] = Field(..., alias="reviewerRole")
    opinion: str
    decision_impact: Literal["supports_pass", "supports_conditional_pass", "supports_hold", "supports_fail"] = Field(
        ...,
        alias="decisionImpact",
    )
    key_concerns: List[str] = Field(default_factory=list, alias="keyConcerns")
    required_actions: List[str] = Field(default_factory=list, alias="requiredActions")
    evidence_ids: List[str] = Field(default_factory=list, alias="evidenceIds")
    confidence: Optional[float] = Field(None, ge=0, le=1)


class ScenarioConclusion(BaseModel):
    """Scenario-level conclusion required by the Chief Deal Orchestrator."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    scenario: Literal["base_case", "downside_case", "upside_case", "kill_case"] = "base_case"
    summary: str
    key_metrics: Dict[str, Any] = Field(default_factory=dict, alias="keyMetrics")
    risk_flags: List[str] = Field(default_factory=list, alias="riskFlags")
    decision_implication: str = Field(..., alias="decisionImplication")


class DecisionGateResult(BaseModel):
    """Pass/hold/fail gate result with required conditions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    decision: Literal["PASS", "CONDITIONAL_PASS", "HOLD", "FAIL"]
    rationale: str
    conditions_precedent: List[str] = Field(default_factory=list, alias="conditionsPrecedent")
    conditions_subsequent: List[str] = Field(default_factory=list, alias="conditionsSubsequent")
    required_supplements: List[str] = Field(default_factory=list, alias="requiredSupplements")
    mitigation_actions: List[str] = Field(default_factory=list, alias="mitigationActions")
    human_review_required: bool = Field(True, alias="humanReviewRequired")


class ChiefDealOrchestrationPlan(BaseModel):
    """Top-level plan for the target AI Deal Room operating model."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: str = Field(CHIEF_DEAL_ORCHESTRATION_SCHEMA_VERSION, alias="schemaVersion")
    project_id: Optional[str] = Field(None, alias="projectId")
    analysis_purpose: Optional[str] = Field(None, alias="analysisPurpose")
    shared_deal_state_graph: SharedDealStateGraph = Field(..., alias="sharedDealStateGraph")
    task_graph: Dict[str, Any] = Field(default_factory=dict, alias="taskGraph")
    agent_pods: List[AgentPodPlan] = Field(default_factory=list, alias="agentPods")
    pod_runs: List[AgentPodRun] = Field(default_factory=list, alias="podRuns")
    scenario_conclusions: List[ScenarioConclusion] = Field(default_factory=list, alias="scenarioConclusions")
    risk_committee: List[RiskCommitteeOpinion] = Field(default_factory=list, alias="riskCommittee")
    decision_gate: Optional[DecisionGateResult] = Field(None, alias="decisionGate")
    report_studio_outputs: List[str] = Field(default_factory=list, alias="reportStudioOutputs")
    learning_loop_events: List[Dict[str, Any]] = Field(default_factory=list, alias="learningLoopEvents")
