from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StressTranche(BaseModel):
    tranche: str = Field(..., description="Tranche identifier, e.g. Senior or Tr.A")
    amount: float = Field(..., description="Tranche principal in KRW")
    priority: int = Field(..., description="Repayment priority, 1 = most senior")
    interestRate: float = Field(default=0.0, description="Annual interest rate, e.g. 0.058")
    feeRate: float = Field(default=0.0, description="Annual fee or other rate")


class StressScenarioDriver(BaseModel):
    key: str = Field(..., description="Scenario key, e.g. base, mild, severe, tail")
    name: str = Field(..., description="Display name, e.g. Base Case")
    saleRate: float = Field(..., description="Cumulative sales rate, e.g. 0.92")
    constructionCostDelta: float = Field(default=0.0, description="Construction cost change, e.g. 0.10 for +10%")
    interestRateDelta: float = Field(default=0.0, description="Interest rate change in absolute terms, e.g. 0.015 for +1.5%p")
    discountRate: float = Field(default=0.0, description="Sale-price discount applied to realised proceeds")


class StressTestSchedule(BaseModel):
    months: int = Field(default=36, description="Total project period in months")
    landPaymentMonth: int = Field(default=0)
    constructionStartMonth: int = Field(default=3)
    constructionEndMonth: int = Field(default=30)
    salesStartMonth: int = Field(default=6)
    salesEndMonth: int = Field(default=36)


class StressTestRequest(BaseModel):
    projectId: Optional[str] = None
    currency: str = "KRW"
    totalRevenue: float = Field(..., description="Total sales revenue at 100% sale rate, no discount")
    landCost: float = Field(default=0.0)
    constructionCost: float = Field(default=0.0, description="Base construction cost")
    otherCost: float = Field(default=0.0, description="Design, sales, tax, and other non-finance cost")
    financeCost: float = Field(default=0.0, description="Base finance cost used when no tranche structure is supplied")
    pfAmount: float = Field(default=0.0, description="Total PF principal; defaults to sum of pfStructure amounts")
    pfStructure: List[StressTranche] = Field(default_factory=list)
    unsoldRecoveryHaircut: float = Field(default=0.2, description="Haircut applied to unsold inventory collateral value")
    hurdleRate: float = Field(default=0.12)
    periodsPerYear: int = Field(default=12)
    salesCollection: str = Field(
        default="10-60-30",
        description="Sales collection template key fed to the Cash Flow Engine for realistic PF-draw timing (e.g. 10-60-30, 10-50-40, forward_sale_closing).",
    )
    schedule: Optional[StressTestSchedule] = None
    scenarios: Optional[List[StressScenarioDriver]] = None


class StressScenarioResult(BaseModel):
    key: str
    name: str
    saleRate: float
    constructionCostDelta: float
    interestRateDelta: float
    discountRate: float
    adjustedRevenue: float
    adjustedConstructionCost: float
    adjustedFinanceCost: float
    adjustedTotalCost: float
    developmentProfit: float
    profitMargin: float
    projectIrr: Optional[float]
    projectIrrStatus: str
    meetsHurdle: bool
    seniorRecoveryRatio: Optional[float]
    seniorStatus: str
    statusLabel: str
    cashflowSource: str = Field(
        default="unknown",
        description="cash_flow_engine when the real engine produced the IRR series, otherwise fallback_uniform.",
    )
    cashflowDiagnostics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Diagnostic metadata explaining the source or fallback reason for scenario cash flows.",
    )


class StressTestResult(BaseModel):
    analysisType: str = "stress_test_v1"
    scenarioCount: int
    hurdleRate: float
    scenarios: List[StressScenarioResult]
    worstCaseKey: Optional[str] = None
    firstLossScenarioKey: Optional[str] = None
    seniorBreakScenarioKey: Optional[str] = None
    riskFlags: List[Dict[str, Any]] = Field(default_factory=list)
    reportFragment: str = ""
    createdAt: str
