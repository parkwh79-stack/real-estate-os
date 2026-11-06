from copy import deepcopy

import pytest

from use_cases.common import deep_merge
from use_cases.feasibility_workbook_llm import (
    FeasibilityReviewConflict, apply_feasibility_input_use_case, feasibility_review_state,
)
from use_cases.workbook_import_approval import persist_feasibility_workbook_candidate_use_case


class Repository:
    def __init__(self, project):
        self.project = deepcopy(project)
        self.updates = 0

    def get(self, _project_id):
        return deepcopy(self.project)

    def update(self, _project_id, patch):
        self.updates += 1
        self.project = deep_merge(self.project, patch)
        return deepcopy(self.project)


def source():
    return {"archiveId": "a" * 64, "originalFileHash": "a" * 64,
            "originalFileName": "상봉 검토.xlsx", "objectVersion": "source-1"}


def mapped_draft(variant="sale"):
    return {"project_id": "p1", "project_name": "상봉 검토", "metrics": {"total_cost": 123456789},
            "raw_import": {"profile": "sangbong-sale-feasibility" if variant == "sale" else "sangbong-private-rental-feasibility",
                "sourceWorkbook": "상봉 검토.xlsx", "sourceDocument": source(),
                "financialMapping": {"schemaVersion": "sangbong-financial-mapping-v1", "variant": variant,
                    "status": "needs_review", "sourceFields": {
                        "total_cost": {"label": "총사업비", "value": 123456789, "rawValue": 123456.789,
                            "formula": "=SUM(K54:K80)", "sourceCell": "'1. 사업수지'!K81", "sourceUnit": "KRW_THOUSAND",
                            "normalizedUnit": "KRW", "status": "needs_review", "calculationState": "saved_formula_cache"},
                        "pf_amount": {"label": "PF 총액", "value": 0, "rawValue": 0, "sourceCell": "'0. 개요입력'!K10",
                            "normalizedUnit": "KRW", "status": "read", "calculationState": "literal"},
                        "finance_cost": {"label": "금융비용", "value": None, "sourceCell": "'1. 사업수지'!K70",
                            "normalizedUnit": "KRW", "status": "missing", "calculationState": "unavailable"},
                        "unrecognized_private_key": {"value": 44}},
                    "limitations": ["수식의 마지막 저장값이며 재계산 미검증입니다."]},
                "financingTermReview": {"schemaVersion": "sangbong-workbook-financing-review-v1", "sourceWorkbook": "상봉 검토.xlsx",
                    "terms": [{"trancheId": "tr-a", "name": "PF", "principalReconciled": False,
                        "fields": {"interest_rate": {"value": .055, "sourceCell": "'0. 개요입력'!J20", "status": "read"}}}]}}}


@pytest.mark.parametrize("variant", ["sale", "rental"])
def test_mapped_candidate_save_reopen_retains_sources_without_promoting_inputs(variant):
    approved = {"approved": "unchanged"}
    repo = Repository({"feasibility_input": {"total_cost": 77}, "canonicalFeasibilityStudyV2": approved})
    draft = mapped_draft(variant)
    result = persist_feasibility_workbook_candidate_use_case(repo, "p1", {
        "feasibilityStudyV2Draft": draft, "sourceDocument": source(),
        "validation": {"status": "failed", "sourceDocumentHash": "a" * 64}}, recorded_by="analyst")
    assert result["authoritative"] is False
    assert repo.project["feasibility_input"]["total_cost"] == 77
    assert repo.project["canonicalFeasibilityStudyV2"] == approved
    state = feasibility_review_state(repo.get("p1"))
    mapping = state["workbookFinancialReview"]
    assert mapping["variant"] == variant
    assert mapping["sourceDocument"] == source()
    assert mapping["sourceFields"]["total_cost"] == draft["raw_import"]["financialMapping"]["sourceFields"]["total_cost"]
    assert mapping["sourceFields"]["pf_amount"]["value"] == 0
    assert mapping["sourceFields"]["finance_cost"]["value"] is None
    assert "unrecognized_private_key" not in mapping["sourceFields"]
    assert state["workbookFinancingReview"]["terms"][0]["fields"]["interest_rate"]["value"] == .055
    assert state["workbookFinancingReview"]["sourceDocument"]["archiveId"] == source()["archiveId"]
    mapping["sourceFields"]["total_cost"]["value"] = 1
    assert feasibility_review_state(repo.get("p1"))["workbookFinancialReview"]["sourceFields"]["total_cost"]["value"] == 123456789


def test_explicit_financial_review_save_preserves_candidate_and_approved_source_then_reopens():
    approved = {"original": True}
    repo = Repository({"feasibilityStudyV2Draft": mapped_draft(), "canonicalFeasibilityStudyV2": approved,
                       "feasibility_input": {"total_cost": 77}})
    initial = feasibility_review_state(repo.project)
    result = apply_feasibility_input_use_case(repo, "p1", {"total_cost": 123456789}, applied_by="analyst",
        expected_review_hash=initial["reviewHash"], field_reviews={"total_cost": {
            "origin": "document_correction", "reason": "천원 단위를 원으로 대조, 저장 수식값의 분석 가정 사용",
            "sourceCell": "'1. 사업수지'!K81"}}, field_source_documents={"total_cost": source()})
    assert result["reviewState"]["values"]["total_cost"] == 123456789
    reopened = feasibility_review_state(repo.get("p1"))
    assert reopened["fieldReviews"]["total_cost"]["sourceCell"] == "'1. 사업수지'!K81"
    assert reopened["workbookFinancialReview"]["sourceFields"]["total_cost"]["rawValue"] == 123456.789
    assert repo.project["canonicalFeasibilityStudyV2"] == approved
    assert repo.project["feasibilityStudyV2Draft"]["raw_import"]["financialMapping"] == mapped_draft()["raw_import"]["financialMapping"]
    before = deepcopy(repo.project)
    with pytest.raises(FeasibilityReviewConflict):
        apply_feasibility_input_use_case(repo, "p1", {"total_cost": 1}, applied_by="analyst",
                                         expected_review_hash=initial["reviewHash"])
    assert repo.project == before


def test_unknown_mapping_and_financing_schemas_are_not_exposed_as_supported_review():
    draft = mapped_draft()
    draft["raw_import"]["profile"] = "unrecognized-profile"
    draft["raw_import"]["financingTermReview"]["schemaVersion"] = "unrecognized-review"
    state = feasibility_review_state({"feasibilityStudyV2Draft": draft})
    assert state["workbookFinancialReview"] is None
    assert state["workbookFinancingReview"] is None
