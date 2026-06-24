from __future__ import annotations

from agents.credit_document_i18n import localize, risk_title_ko, senior_status_ko, severity_ko


def test_senior_status_korean():
    assert senior_status_ko("credit_enhancement_triggered") == "신용보강 발동"
    assert senior_status_ko("normal") == "정상"
    assert senior_status_ko("senior_principal_loss_possible") == "원금 일부 손실 가능"


def test_severity_korean():
    assert severity_ko("high") == "높음"
    assert severity_ko("medium") == "중간"


def test_risk_code_to_korean_title():
    assert risk_title_ko({"code": "UNSOLD_LTV_BREACH"}) == "미분양 LTV 한도 초과"
    assert risk_title_ko({"code": "NO_COMPARABLE_DATA"}) == "비교사례 자료 부족"
    # falls back to localized title when code unknown
    assert risk_title_ko({"code": None, "title": "Low presale rate"}) == "분양률 저조"


def test_localize_phrases_and_codes():
    out = localize("R-EXIT-001 UNSOLD_LTV_BREACH: Review waterfall, unsold LTV, discount-sale trigger, and tranche recovery scenarios.")
    assert "미분양 LTV 한도 초과" in out
    assert "워터폴" in out
    assert "UNSOLD_LTV_BREACH" not in out
    assert "Review waterfall" not in out


def test_localize_leaves_unknown_text():
    assert localize("순수 한국어 문장") == "순수 한국어 문장"
    assert localize("Totally novel english") == "Totally novel english"
