from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AssumptionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(alias="proposalId")
    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId")
    field_path: str = Field(alias="fieldPath")
    current_value: Any = Field(default=None, alias="currentValue")
    proposed_value: Any = Field(alias="proposedValue")
    source_agent: Optional[str] = Field(default=None, alias="sourceAgent")
    rationale: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list, alias="evidenceIds")
    source_locator: Dict[str, Any] = Field(default_factory=dict, alias="sourceLocator")
    status: Literal["pending", "approved", "rejected", "superseded"] = "pending"
    created_at: str = Field(alias="createdAt")
    reviewed_at: Optional[str] = Field(default=None, alias="reviewedAt")
    reviewed_by: Optional[str] = Field(default=None, alias="reviewedBy")
    review_comment: Optional[str] = Field(default=None, alias="reviewComment")


class AssumptionProposalApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_ids: List[str] = Field(alias="proposalIds")
    comment: str = ""
