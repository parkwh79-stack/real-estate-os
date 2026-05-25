from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProductMixItem(BaseModel):
    type: str = Field(..., description="Product type name, e.g., 'A', 'Apartment', 'Officetel'")
    units: int = Field(..., description="Number of units in this product")
    avgUnitPrice: float = Field(..., description="Average unit sale price in KRW")
    totalRevenue: float = Field(..., description="Total sales revenue for this product in KRW")


class TrancheItem(BaseModel):
    tranche: str = Field(..., description="Tranche identifier, e.g., 'Tr.A', 'Senior', 'Mezzanine'")
    amount: float = Field(..., description="Tranche principal amount in KRW")
    priority: int = Field(..., description="Repayment priority ranking (lower = higher priority)")
    interestRate: float = Field(..., description="Annual interest rate, e.g. 0.065 for 6.5%")
    feeRate: float = Field(..., description="Annual fee/other rate, e.g. 0.01 for 1%")


class ScenarioDrivers(BaseModel):
    saleRates: List[float] = Field(default_factory=lambda: [0.4, 0.5, 0.6, 0.7, 0.8, 1.0])
    discountRates: List[float] = Field(default_factory=lambda: [0.0, 0.05, 0.1, 0.15, 0.2])
    unsoldRates: List[float] = Field(default_factory=lambda: [0.0, 0.1, 0.2, 0.3, 0.4])
    recoveryHaircuts: List[float] = Field(default_factory=lambda: [0.1, 0.2, 0.3])
    additionalPriorityCosts: float = Field(default=0.0, description="Priority taxes or development fees")


class WaterfallRule(BaseModel):
    priorityOrder: List[str] = Field(
        default_factory=lambda: ["tax", "essential_cost", "senior_interest", "senior_principal", "mezzanine", "junior", "equity"]
    )
    tranchePriorityField: str = Field(default="priority")
    includeInterest: bool = Field(default=True)
    includeFees: bool = Field(default=True)


class SourceMetadata(BaseModel):
    sourceName: str
    sourceType: str
    sourceUrlOrEvidenceId: Optional[str] = None
    baseDate: Optional[str] = None
    collectedAt: Optional[str] = None
    licenseOrUsageNote: Optional[str] = None
    confidence: float = 1.0


class ScenarioSetRequest(BaseModel):
    scenarioSetId: Optional[str] = None
    scenarioSetName: Optional[str] = None
    baseDate: Optional[str] = None
    currency: str = "KRW"
    unit: str = "KRW"
    projectId: Optional[str] = None
    productMix: List[ProductMixItem]
    pfStructure: List[TrancheItem]
    scenarioDrivers: Optional[ScenarioDrivers] = None
    waterfallRule: Optional[WaterfallRule] = None
    sourceMetadata: Optional[SourceMetadata] = None


class WaterfallResultRow(BaseModel):
    rank: int
    tranche: str
    priority: int
    principalClaim: float
    interestClaim: float
    feeClaim: float
    totalClaim: float
    paymentReceived: float
    shortfall: float
    recoveryRatio: float
    status: str


class WaterfallResult(BaseModel):
    scenarioId: str
    saleRate: Optional[float] = None
    discountRate: Optional[float] = None
    cashAvailable: float
    totalClaimAmount: float
    remainingCash: float
    totalShortfall: float
    rows: List[WaterfallResultRow]


class UnsoldLtvRow(BaseModel):
    saleRate: float
    unsoldRate: float
    unsoldUnits: int
    discountRate: float
    grossUnsoldValue: float
    discountedUnsoldValue: float
    unsoldLtv: float
    riskLevel: str


class UnsoldLtvMatrix(BaseModel):
    matrixType: str = "unsold_ltv_matrix_v1"
    pfAmount: float
    totalUnits: int
    avgUnitPrice: float
    rows: List[UnsoldLtvRow]
    summary: Dict[str, Any]


class TrancheRecoveryRow(BaseModel):
    scenarioId: str
    saleRate: float
    discountRate: float
    recoveryPool: float
    tranches: List[Dict[str, Any]]


class ScenarioRiskFlag(BaseModel):
    code: str
    severity: str
    message: str


class ScenarioReportFragments(BaseModel):
    waterfallSummary: str
    unsoldLtvSummary: str
    trancheRecoverySummary: str
    approvalCondition: str


class ScenarioSetResult(BaseModel):
    analysisType: str = "calculation_scenario_engine_v1"
    scenarioSetId: str
    status: str = "completed"
    dataStatus: str = "complete"
    waterfallScenarios: List[WaterfallResult]
    unsoldLtvMatrix: UnsoldLtvMatrix
    trancheRecoveryMatrix: List[TrancheRecoveryRow]
    riskFlags: List[ScenarioRiskFlag]
    reportFragments: ScenarioReportFragments
    sourceMetadata: Optional[SourceMetadata] = None
    createdAt: str
