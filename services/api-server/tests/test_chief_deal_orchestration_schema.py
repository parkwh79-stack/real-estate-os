import pytest
from pydantic import ValidationError

from schemas.orchestration import (
    AgentPodPlan,
    ChiefDealOrchestrationPlan,
    DealStateValue,
    DecisionGateResult,
    RiskCommitteeOpinion,
    ScenarioConclusion,
    SharedDealStateGraph,
)


def test_chief_deal_orchestration_plan_contract():
    state = SharedDealStateGraph(
        projectId="p1",
        assumptionBook=[
            DealStateValue(
                key="feasibility.totalRevenue",
                value=100000000000,
                valueType="source",
                unit="KRW",
                confidence=0.98,
                evidenceIds=["ev-sales-001"],
            )
        ],
    )
    plan = ChiefDealOrchestrationPlan(
        projectId="p1",
        analysisPurpose="pf_credit_review",
        sharedDealStateGraph=state,
        agentPods=[
            AgentPodPlan(
                podKey="land_legal",
                name="Land-Legal Pod",
                objective="토지·권리·법규 개발가능성 검토",
                agents=["LandAgent", "RegulationAgent", "RightsEncumbranceAgent"],
                expectedOutputs=["parcel_analysis", "permit_risk", "title_risk"],
            )
        ],
        scenarioConclusions=[
            ScenarioConclusion(
                scenario="kill_case",
                summary="인허가 지연과 선순위 shortfall이 동시에 발생하는 경우",
                keyMetrics={"seniorShortfall": 5000000000},
                riskFlags=["SENIOR_SHORTFALL"],
                decisionImplication="FAIL unless additional equity or senior reduction is committed.",
            )
        ],
        riskCommittee=[
            RiskCommitteeOpinion(
                reviewerRole="devils_advocate",
                opinion="시장가격 근거가 부족한 분양가 가정은 승인 전 보강 필요",
                decisionImpact="supports_hold",
                keyConcerns=["PROPOSED_PRICE_BASIS_MISSING"],
                requiredActions=["비교사례와 분양가 기준표 제출"],
                evidenceIds=["ev-risk-001"],
                confidence=0.9,
            )
        ],
        decisionGate=DecisionGateResult(
            decision="HOLD",
            rationale="토지 증빙과 분양가 근거가 부족하여 최종 승인 판단 보류",
            requiredSupplements=["공시지가 evidence", "분양가 비교사례"],
        ),
    )

    dumped = plan.model_dump(by_alias=True)
    assert dumped["schemaVersion"] == "realytics-chief-deal-orchestration-v1"
    assert dumped["sharedDealStateGraph"]["assumptionBook"][0]["valueType"] == "source"
    assert dumped["agentPods"][0]["podKey"] == "land_legal"
    assert dumped["riskCommittee"][0]["reviewerRole"] == "devils_advocate"
    assert dumped["decisionGate"]["decision"] == "HOLD"


@pytest.mark.parametrize("revision", ["x" * 64, "a" * 63, "a" * 65, "A" * 64, 123])
def test_plan_rejects_invalid_preflight_revision(revision):
    with pytest.raises(ValidationError):
        ChiefDealOrchestrationPlan(
            projectId="p", sharedDealStateGraph={"projectId": "p"},
            preflightInputRevision=revision,
        )


def test_plan_preflight_revision_round_trips_without_accepting_unknown_fields():
    data = {
        "projectId": "p", "sharedDealStateGraph": {"projectId": "p"},
        "preflightInputRevision": "a" * 64,
    }
    plan = ChiefDealOrchestrationPlan.model_validate(data)
    assert plan.model_dump(by_alias=True)["preflightInputRevision"] == data["preflightInputRevision"]
    with pytest.raises(ValidationError):
        ChiefDealOrchestrationPlan.model_validate({**data, "unknownPreflightValue": True})
