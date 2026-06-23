from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


CREDIT_ENHANCEMENT_TYPES = (
    "responsible_completion",
    "debt_assumption",
    "trust_management",
    "cash_sweep_reserve",
    "equity_lockup",
    "sponsor_guarantee",
)


class CreditEnhancementInput(BaseModel):
    type: str = Field(..., description="One of CREDIT_ENHANCEMENT_TYPES")
    present: bool = Field(default=True, description="Whether the measure is in place")
    provider: Optional[str] = Field(default=None, description="Counterparty or provider, e.g. contractor or sponsor")
    seniorLtvDeltaPp: Optional[float] = Field(default=None, description="Override: senior LTV reduction in percentage points")
    gradeNotch: Optional[int] = Field(default=None, description="Override: credit-grade upgrade notches")
    dscrSupport: Optional[float] = Field(default=None, description="Override: DSCR uplift")
    parameters: Dict[str, Any] = Field(default_factory=dict)
    evidenceIds: List[str] = Field(default_factory=list)
    sourceLocators: List[Dict[str, Any]] = Field(default_factory=list)


class CreditEnhancementRequest(BaseModel):
    projectId: Optional[str] = None
    baseSeniorLtv: float = Field(default=0.0, description="Senior LTV before enhancement, e.g. 0.60")
    baseDscr: Optional[float] = Field(default=None, description="DSCR before enhancement")
    baseCreditGrade: Optional[str] = Field(default=None, description="Base credit grade letter, e.g. BBB")
    enhancements: List[CreditEnhancementInput] = Field(default_factory=list)


class CreditEnhancementMeasureResult(BaseModel):
    type: str
    nameKo: str
    category: str
    status: str  # active | missing | needs_evidence
    present: bool
    seniorLtvDeltaPp: float
    gradeNotch: int
    dscrSupport: float
    riskMitigated: str
    effectSummary: str
    requiredEvidence: List[str]
    recommendation: Optional[str] = None
    evidenceIds: List[str] = Field(default_factory=list)
    sourceLocators: List[Dict[str, Any]] = Field(default_factory=list)


class CreditEnhancementResult(BaseModel):
    analysisType: str = "credit_enhancement_v1"
    baseSeniorLtv: float
    adjustedSeniorLtv: float
    totalSeniorLtvDeltaPp: float
    totalGradeNotch: int
    baseDscr: Optional[float] = None
    adjustedDscr: Optional[float] = None
    activeCount: int
    coverageRatio: float
    enhancementGrade: str
    measures: List[CreditEnhancementMeasureResult]
    missingCriticalMeasures: List[str]
    riskFlags: List[Dict[str, Any]] = Field(default_factory=list)
    reportParagraph: str
    createdAt: str
