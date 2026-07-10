import storage
from use_cases.approval_readiness_gate import build_deal_state_approval_readiness


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "gate.sqlite3"))


def _gate(result, code):
    return next((g for g in result["gates"] if g["code"] == code), None)


def test_blocking_pod_ai_review_finding_blocks_readiness(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    # A deterministic-grounded blocking finding (senior shortfall risk_note).
    storage.upsert_pod_ai_review_record("p1", {
        "reviewId": "r1", "podKey": "pf_credit", "reviewStatus": "blocking",
        "findings": [{"category": "risk_note", "severity": "blocking", "message": "선순위 원금 부족"}],
    })
    result = build_deal_state_approval_readiness("p1")
    g = _gate(result, "POD_AI_REVIEW_BLOCKERS_CLEARED")
    assert g is not None and g["pass"] is False
    assert result["podAiReviewBlockerCount"] == 1


def test_llm_opinion_only_finding_does_not_block(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    # A non-deterministic category (guardrail/info) must never gate readiness.
    storage.upsert_pod_ai_review_record("p2", {
        "reviewId": "r2", "podKey": "market_product", "reviewStatus": "blocking",
        "findings": [{"category": "guardrail", "severity": "blocking", "message": "LLM opinion only"}],
    })
    result = build_deal_state_approval_readiness("p2")
    g = _gate(result, "POD_AI_REVIEW_BLOCKERS_CLEARED")
    assert g["pass"] is True
    assert result["podAiReviewBlockerCount"] == 0


def test_resolved_review_does_not_block(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    storage.upsert_pod_ai_review_record("p3", {
        "reviewId": "r3", "podKey": "pf_credit", "reviewStatus": "approved",
        "findings": [{"category": "risk_note", "severity": "blocking", "message": "해소됨"}],
    })
    result = build_deal_state_approval_readiness("p3")
    assert _gate(result, "POD_AI_REVIEW_BLOCKERS_CLEARED")["pass"] is True


def test_no_reviews_gate_passes(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = build_deal_state_approval_readiness("p4")
    assert _gate(result, "POD_AI_REVIEW_BLOCKERS_CLEARED")["pass"] is True


def test_llm_findings_do_not_gate_only_deterministic_do(monkeypatch, tmp_path):
    """When an LLM supplies the display findings, the gate must judge from the
    review's deterministicFindings — an LLM blocking opinion alone never gates."""
    _setup(monkeypatch, tmp_path)
    storage.upsert_pod_ai_review_record("p5", {
        "reviewId": "r5", "podKey": "pf_credit", "reviewStatus": "blocking",
        "reviewMode": "llm_evidence_aware_review",
        # LLM opinion says blocking, but deterministic layer found nothing blocking.
        "findings": [{"category": "risk_note", "severity": "blocking", "message": "LLM says risky"}],
        "deterministicFindings": [{"category": "risk_note", "severity": "high", "message": "deterministic high only"}],
    })
    result = build_deal_state_approval_readiness("p5")
    # deterministicFindings has no blocking -> gate passes (LLM blocking ignored).
    assert _gate(result, "POD_AI_REVIEW_BLOCKERS_CLEARED")["pass"] is True
    assert result["podAiReviewBlockerCount"] == 0


def test_deterministic_blocking_gates_even_with_nonwhitelisted_category(monkeypatch, tmp_path):
    """market_product-style categories (not in the old whitelist) still gate when
    deterministic + blocking — the category whitelist no longer drops them."""
    _setup(monkeypatch, tmp_path)
    storage.upsert_pod_ai_review_record("p6", {
        "reviewId": "r6", "podKey": "market_product", "reviewStatus": "blocking",
        "deterministicFindings": [{"category": "proposed_price_basis_missing", "severity": "blocking", "message": "분양가 기준 없음"}],
    })
    result = build_deal_state_approval_readiness("p6")
    assert _gate(result, "POD_AI_REVIEW_BLOCKERS_CLEARED")["pass"] is False
    assert result["podAiReviewBlockerCount"] == 1


def test_malformed_findings_do_not_crash_gate(monkeypatch, tmp_path):
    """A review row with non-dict findings must not crash approval readiness."""
    _setup(monkeypatch, tmp_path)
    storage.upsert_pod_ai_review_record("p7", {
        "reviewId": "r7", "podKey": "pf_credit", "reviewStatus": "needs_human_review",
        "findings": ["freeform note", None, 42],
    })
    result = build_deal_state_approval_readiness("p7")  # must not raise
    assert _gate(result, "POD_AI_REVIEW_BLOCKERS_CLEARED")["pass"] is True
