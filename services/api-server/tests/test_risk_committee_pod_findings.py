from use_cases.chief_deal_orchestration import _risk_committee_opinions


def _by_role(committee):
    return {o.reviewer_role: o for o in committee}


def _base_args():
    return ([], [], [])  # missing, conflicts, scenarios


def test_blocking_pod_finding_escalates_relevant_reviewer():
    missing, conflicts, scenarios = _base_args()
    pod_findings = {
        "pf_credit": [
            {"category": "risk_note", "severity": "blocking", "message": "선순위 원금 부족",
             "recommendedAction": "회수 재원 보강 후 재계산"},
        ],
    }
    committee = _risk_committee_opinions({}, missing, conflicts, scenarios, pod_findings)
    by_role = _by_role(committee)

    # credit_reviewer is accountable for pf_credit -> escalated to at least HOLD,
    # with the finding folded into concerns + actions.
    credit = by_role["credit_reviewer"]
    assert credit.decision_impact in {"supports_hold", "supports_fail"}
    assert any("선순위 원금 부족" in c for c in credit.key_concerns)
    assert any("회수 재원 보강" in a for a in credit.required_actions)

    # market_stress_tester is NOT accountable for pf_credit -> unchanged, conditional pass.
    market = by_role["market_stress_tester"]
    assert market.decision_impact == "supports_conditional_pass"
    assert not any("선순위" in c for c in market.key_concerns)


def test_high_only_finding_adds_concern_without_escalating():
    committee = _risk_committee_opinions(
        {}, [], [], [],
        {"market_product": [{"category": "missing_evidence", "severity": "high", "message": "비교사례 부족"}]},
    )
    market = _by_role(committee)["market_stress_tester"]
    assert market.decision_impact == "supports_conditional_pass"  # high does not escalate
    assert any("비교사례 부족" in c for c in market.key_concerns)  # but is surfaced


def test_no_pod_findings_is_backward_compatible():
    a = _risk_committee_opinions({}, [], [], [])
    b = _risk_committee_opinions({}, [], [], [], {})
    assert len(a) == len(b) == 7
    assert all(o.decision_impact == "supports_conditional_pass" for o in a)
