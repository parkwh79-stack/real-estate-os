from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


PF_REVIEW_WORKFLOW = "pf-review"
AGENT_HANDOFF_SCHEMA_VERSION = "realytics-agent-handoff-v1"
AGENT_PROGRESS_SCHEMA_VERSION = "realytics-agent-progress-v1"

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
    ("legal_permit_review", "Legal and permit pre-review"),
    ("risk_assessment", "Risk assessment"),
    ("risk_register_generation", "Risk register generation"),
    ("credit_review_decision", "Credit review decision"),
    ("scenario_restructuring_plan", "Scenario restructuring plan"),
    ("phase11_report_generation", "Phase 11 report generation"),
    ("evidence_graph_generation", "Evidence graph generation"),
    ("quality_check", "Report completeness quality check"),
    ("approval_readiness_gate", "Approval readiness gate"),
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
