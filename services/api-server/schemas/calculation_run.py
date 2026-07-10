"""Canonical CalculationRun contract shared by deterministic engines."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CalculationRunMetric(BaseModel):
    key: str
    value: Any = None
    unit: Optional[str] = None
    sourceLocator: Dict[str, Any] = Field(default_factory=dict)


class CalculationRun(BaseModel):
    schemaVersion: str = "realytics-calculation-run-v2"
    projectId: str
    calculationRunId: str
    engineKey: str
    engineVersion: str
    status: str = "completed"
    inputEvidenceIds: List[str] = Field(default_factory=list)
    assumptionIds: List[str] = Field(default_factory=list)
    outputMetrics: List[CalculationRunMetric] = Field(default_factory=list)
    riskIssueIds: List[str] = Field(default_factory=list)
    riskIssues: List[Dict[str, Any]] = Field(default_factory=list)
    sourceLocator: Dict[str, Any] = Field(default_factory=dict)
    scenario: Dict[str, Any] = Field(default_factory=dict)
    artifactRef: Dict[str, Any] = Field(default_factory=dict)
    createdAt: datetime
    updatedAt: datetime
