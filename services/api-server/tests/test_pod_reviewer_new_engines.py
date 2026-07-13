"""Pod AI Reviewer now consumes the Product Planning and Covenant/EOD engine
outputs. These tests exercise the deterministic findings directly (no LLM /
network), which are the same findings the LLM-evidence-aware review builds on."""

from agents.pod_ai_reviewer_agent import _market_product_findings, _pf_credit_findings


def test_market_product_review_flags_product_planning_without_price():
    artifact = {
        "productPlanning": {"pricedCount": 0, "rows": [{"assumptionBasis": "planning_benchmark"}]},
    }
    findings = _market_product_findings(artifact, "art-market", {}, {})
    msgs = " ".join(f["message"] for f in findings)
    assert "상품기획" in msgs and ("분양수입" in msgs or "NOI" in msgs)


def test_market_product_review_flags_benchmark_only_program():
    artifact = {
        "productPlanning": {
            "pricedCount": 2,
            "rows": [{"assumptionBasis": "planning_benchmark"}, {"assumptionBasis": "user_input"}],
        },
    }
    findings = _market_product_findings(artifact, "art-market", {}, {})
    assert any("계획 벤치마크" in f["message"] for f in findings)


def test_pf_credit_review_flags_breached_covenant():
    artifact = {
        "covenantEod": {
            "covenants": [
                {"label": "LTV 상한", "breached": True, "currentValue": 0.9, "thresholdValue": 0.8, "breachAction": "추가담보 제공"},
            ],
        },
    }
    findings = _pf_credit_findings(artifact, "art-pf", {})
    risk_notes = [f for f in findings if f["category"] == "risk_note"]
    assert any("LTV 상한" in f["message"] and f["severity"] == "high" for f in risk_notes)


def test_pf_credit_review_flags_needs_evidence_covenant():
    artifact = {
        "covenantEod": {
            "covenants": [
                {"label": "시공사 책임준공 확약", "status": "needs_evidence"},
            ],
        },
    }
    findings = _pf_credit_findings(artifact, "art-pf", {})
    assert any(f["category"] == "missing_evidence" and "책임준공" in f["message"] for f in findings)


def test_pf_credit_review_uses_derived_covenant_over_missing_flag():
    # When the derived covenantEod is present (all active), the generic
    # "covenant/EOD 조항 근거 미확인" missing-evidence finding is NOT emitted.
    artifact = {
        "covenantEod": {"covenants": [{"label": "목표 분양률", "status": "active"}]},
    }
    findings = _pf_credit_findings(artifact, "art-pf", {})
    assert not any("Covenant / EOD(기한이익상실) / CP(선행조건)" in f["message"] for f in findings)
