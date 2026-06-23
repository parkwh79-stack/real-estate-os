from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CashSweepTranche(BaseModel):
    tranche: str
    amount: float = Field(..., description="Outstanding principal in KRW")
    priority: int = Field(..., description="Repayment priority (1 = most senior)")
    interestRate: float = Field(default=0.0, description="Annual interest rate, e.g. 0.058")
    feeRate: float = Field(default=0.0, description="Annual fee rate")


class CashSweepRequest(BaseModel):
    projectId: Optional[str] = None
    monthlyOperatingCashflow: List[float] = Field(
        ..., description="Monthly cash available before debt service (positive = surplus)"
    )
    tranches: List[CashSweepTranche]
    sweepRatio: float = Field(default=1.0, description="Fraction of post-reserve surplus swept to principal")
    reserveTarget: float = Field(default=0.0, description="Target reserve (DSRA) balance to maintain")
    periodsPerYear: int = Field(default=12)


class CashSweepMonthRow(BaseModel):
    month: int
    openingBalance: float
    interestAccrued: float
    interestPaid: float
    interestShortfall: float
    reserveContribution: float
    reserveBalance: float
    sweepAvailable: float
    principalPaid: float
    endingBalance: float
    tranchePrincipalPaid: Dict[str, float]
    trancheEndingBalance: Dict[str, float]


class CashSweepResult(BaseModel):
    analysisType: str = "cash_sweep_v1"
    months: int
    sweepRatio: float
    reserveTarget: float
    totalInterestPaid: float
    totalInterestShortfall: float
    totalPrincipalSwept: float
    endingReserveBalance: float
    endingTotalBalance: float
    trancheFullRepaidMonth: Dict[str, Optional[int]]
    rows: List[CashSweepMonthRow]
    summary: str
    createdAt: str
