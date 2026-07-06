from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class WorkflowOption(BaseModel):
    key: str
    nameKo: str
    description: str
    availableNow: bool
    recommended: bool = False
    agents: List[str] = Field(default_factory=list)
    missingData: List[str] = Field(default_factory=list)
    endpoint: str = ""


class WorkOrderOutputOption(BaseModel):
    key: str
    nameKo: str
    description: str
    defaultSelected: bool = False


class WorkOrderValidationOption(BaseModel):
    key: str
    nameKo: str
    description: str
    defaultSelected: bool = True


class WorkOrderPromptDraft(BaseModel):
    title: str = "Excel/AI 작업지시서 초안"
    body: str
    intendedTool: str = "Claude in Excel / ChatGPT / 내부 Agent"


class OrchestrationPlanResult(BaseModel):
    analysisType: str = "orchestration_plan_v1"
    projectId: Optional[str] = None
    workflowTrack: Optional[str] = None
    trackLabel: Optional[str] = None
    trackWorkflow: Optional[Dict[str, Any]] = None
    dataAvailability: Dict[str, bool]
    workflows: List[WorkflowOption]
    outputOptions: List[WorkOrderOutputOption] = Field(default_factory=list)
    validationOptions: List[WorkOrderValidationOption] = Field(default_factory=list)
    promptDraft: Optional[WorkOrderPromptDraft] = None
    recommendedWorkflow: Optional[str] = None
    promptKo: str = ""
    summary: str = ""
    createdAt: str
