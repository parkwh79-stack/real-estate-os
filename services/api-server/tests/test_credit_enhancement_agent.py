import pytest

from agents.credit_enhancement_agent import CreditEnhancementAgent
from schemas.credit_enhancement import (
    CREDIT_ENHANCEMENT_TYPES,
    CreditEnhancementInput,
    CreditEnhancementRequest,
)


def _measure(measure_type, **kw):
    return CreditEnhancementInput(type=measure_type, **kw)


def test_no_enhancements_lists_all_six_as_missing():
    result = CreditEnhancementAgent().evaluate(CreditEnhancementRequest(baseSeniorLtv=0.60))
    assert len(result.measures) == 6
    assert {m.type for m in result.measures} == set(CREDIT_ENHANCEMENT_TYPES)
    assert all(m.status == "missing" for m in result.measures)
    assert result.activeCount == 0
    assert result.adjustedSeniorLtv == 0.60  # no effect applied
    assert result.enhancementGrade == "E"
    assert "responsible_completion" in result.missingCriticalMeasures


def test_active_responsible_completion_reduces_senior_ltv():
    result = CreditEnhancementAgent().evaluate(CreditEnhancementRequest(
        baseSeniorLtv=0.60,
        enhancements=[_measure("responsible_completion", evidenceIds=["ev-rc-1"])],
    ))
    rc = next(m for m in result.measures if m.type == "responsible_completion")
    assert rc.status == "active"
    assert rc.seniorLtvDeltaPp == 5.0
    assert result.adjustedSeniorLtv == pytest.approx(0.55)
    assert result.totalSeniorLtvDeltaPp == 5.0


def test_present_without_evidence_needs_evidence_and_no_effect():
    result = CreditEnhancementAgent().evaluate(CreditEnhancementRequest(
        baseSeniorLtv=0.60,
        enhancements=[_measure("debt_assumption", present=True)],  # no evidence
    ))
    da = next(m for m in result.measures if m.type == "debt_assumption")
    assert da.status == "needs_evidence"
    assert da.gradeNotch == 0
    assert result.totalGradeNotch == 0
    assert any(f["code"] == "CREDIT_ENHANCEMENT_NEEDS_EVIDENCE" for f in result.riskFlags)


def test_debt_assumption_grade_notch_and_dscr_support():
    result = CreditEnhancementAgent().evaluate(CreditEnhancementRequest(
        baseSeniorLtv=0.55,
        baseDscr=1.20,
        enhancements=[
            _measure("debt_assumption", evidenceIds=["ev-da"]),
            _measure("cash_sweep_reserve", evidenceIds=["ev-cs"]),
        ],
    ))
    assert result.totalGradeNotch == 2
    assert result.adjustedDscr == pytest.approx(1.35)  # 1.20 + 0.15
    assert result.activeCount == 2


def test_full_six_measures_grade_a_and_no_critical_missing():
    enhancements = [_measure(t, evidenceIds=[f"ev-{t}"]) for t in CREDIT_ENHANCEMENT_TYPES]
    result = CreditEnhancementAgent().evaluate(CreditEnhancementRequest(
        baseSeniorLtv=0.60, baseDscr=1.30, enhancements=enhancements,
    ))
    assert result.activeCount == 6
    assert result.coverageRatio == 1.0
    assert result.enhancementGrade == "A"
    assert result.missingCriticalMeasures == []
    # responsible_completion 5pp + sponsor_guarantee 2pp = 7pp
    assert result.totalSeniorLtvDeltaPp == pytest.approx(7.0)
    assert result.adjustedSeniorLtv == pytest.approx(0.53)


def test_missing_critical_caps_grade_at_c():
    # Four non-critical measures active, but the two critical ones missing.
    active_types = ["debt_assumption", "cash_sweep_reserve", "equity_lockup", "sponsor_guarantee"]
    enhancements = [_measure(t, evidenceIds=[f"ev-{t}"]) for t in active_types]
    result = CreditEnhancementAgent().evaluate(CreditEnhancementRequest(
        baseSeniorLtv=0.60, enhancements=enhancements,
    ))
    assert result.activeCount == 4
    # Without the cap this would be "B"; critical missing caps to "C".
    assert result.enhancementGrade == "C"
    assert set(result.missingCriticalMeasures) == {"responsible_completion", "trust_management"}
