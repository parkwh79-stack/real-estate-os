from use_cases.cashflow_export_policy import evaluate_cashflow_export_policy


def test_draft_download_is_never_report_approval():
    result = evaluate_cashflow_export_policy({}, "draft", lambda: {"pass": True})
    assert result["downloadEligible"] is True
    assert result["reportEligible"] is False


def test_approved_export_requires_gate_evidence_and_report_eligible_calculation():
    anchor = {"financialInputHash": "a" * 64, "inputEvidenceIds": ["e1"], "resultSnapshot": {"reportEligible": True}}
    assert evaluate_cashflow_export_policy({"cash_flow_engine": anchor}, "approved", lambda: {"pass": True})["reportEligible"]
    assert not evaluate_cashflow_export_policy({"cash_flow_engine": anchor}, "approved", lambda: {"pass": False})["downloadEligible"]
    anchor["inputEvidenceIds"] = []
    assert not evaluate_cashflow_export_policy({"cash_flow_engine": anchor}, "approved", lambda: {"pass": True})["downloadEligible"]


def test_approved_export_gate_failure_is_secret_safe():
    def fail():
        raise RuntimeError("private DSN")
    result = evaluate_cashflow_export_policy({}, "approved", fail)
    assert not result["downloadEligible"]
    assert "private" not in str(result)


def test_approved_export_rejects_missing_or_mixed_input_hashes():
    anchor = {"inputEvidenceIds": ["e1"], "resultSnapshot": {"reportEligible": True}}
    assert not evaluate_cashflow_export_policy({"cash_flow_engine": anchor}, "approved", lambda: {"pass": True})['downloadEligible']
    anchor['financialInputHash'] = 'a' * 64
    result = evaluate_cashflow_export_policy({'cash_flow_engine': anchor, 'waterfall': {'financialInputHash': 'b' * 64}}, 'approved', lambda: {'pass': True})
    assert result['failureCodes'] == ['financial_input_lineage_required']


def test_statutory_cost_review_stays_draft_even_if_other_approval_gates_pass():
    anchor = {
        "financialInputHash": "a" * 64,
        "inputEvidenceIds": ["e1"],
        "inputSnapshot": {"statutoryCostSelection": {"analysisOnly": True}},
        "resultSnapshot": {"reportEligible": True},
    }
    runs = {"cash_flow_engine": anchor}
    draft = evaluate_cashflow_export_policy(runs, "draft", lambda: {"pass": True})
    assert draft["downloadEligible"] is True
    assert draft["reportEligible"] is False
    approved = evaluate_cashflow_export_policy(runs, "approved", lambda: {"pass": True})
    assert approved["downloadEligible"] is False
    assert approved["failureCodes"] == ["cashflow_statutory_cost_review_only"]
