from copy import deepcopy
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient

from main import app
from storage import get_project_record
from use_cases.common import deep_merge
from use_cases.feasibility_workbook_llm import (
    FeasibilityReviewConflict,
    apply_feasibility_input_use_case,
    feasibility_review_state,
)


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


def tranche(**changes):
    return {"tranche_id": "senior-1", "name": "선순위", "priority": "senior",
            "amount": None, "interest_rate": None, "fee_rate": None,
            "term_months": None, "repayment_source": None, **changes}


def review(**changes):
    return {"origin": "user_assumption", "reason": "원본 확인 전 분석 가정", **changes}


def apply(repo, terms, *, reviews=None, **kwargs):
    return apply_feasibility_input_use_case(
        repo, "1", {}, applied_by="reviewer",
        expected_review_hash=feasibility_review_state(repo.project)["reviewHash"],
        terms=terms, term_reviews=reviews or {key: review() for key in terms}, **kwargs,
    )


def test_terms_preserve_metadata_zero_and_unknown_schedule_rows_in_one_save():
    canonical = {"schemaVersion": "approved-original", "schedule": [{"key": "completion", "planned_date": "2030-01-01"}]}
    repo = Repository({"schedule": [
        {"key": "completion", "planned_date": "2027-01-01", "provenance": {"source": "original"}},
        {"key": "special_approval", "planned_date": "2027-02-01", "custom": 1},
    ], "pf_structure": [{"tranche": "기존", "amount": 7, "interestRate": 0,
                            "feeRate": 0, "maturityMonths": 24, "excel_row_id": "Finance!9"}],
        "canonicalFeasibilityStudyV2": canonical, "_meta": {"tenantId": "t1"}})
    before = feasibility_review_state(repo.project)
    legacy = before["terms"]["financing"][0]
    assert legacy["tranche_id"] == "row-1"
    assert legacy["priority"] == ""
    assert legacy["interest_rate"] == legacy["fee_rate"] == 0
    changed = {**legacy, "priority": "senior", "amount": 0, "repayment_source": None}
    result = apply(repo, {"schedule": {"completion": "2028-01-01"}, "financing": [changed]})
    assert repo.updates == 1
    assert result["reviewState"]["terms"]["financing"][0]["amount"] == 0
    assert repo.project["pf_structure"][0]["excel_row_id"] == "Finance!9"
    assert repo.project["pf_structure"][0]["interest_rate"] == 0
    assert repo.project["pf_structure"][0]["repayment_source"] is None
    assert repo.project["schedule"][0]["provenance"] == {"source": "original"}
    assert repo.project["schedule"][1] == {"key": "special_approval", "planned_date": "2027-02-01", "custom": 1}
    assert repo.project["canonicalFeasibilityStudyV2"] == canonical
    assert repo.project["_meta"]["tenantId"] == "t1"
    assert result["reviewState"]["canonicalStudyPresent"] is True
    for saved in result["reviewState"]["termReviews"].values():
        assert saved["needsReview"] is True
        assert saved["approvalStatus"] == "not_requested"
        assert saved["reviewedBy"] == "reviewer"


def test_cleared_legacy_aliases_do_not_resurrect_values_in_real_agent_adapter():
    from agents.development_feasibility_agent import DevelopmentFeasibilityAgent

    repo = Repository({"pf_structure": [{"tranche": "Old name", "priority": "senior", "amount": 20,
                                         "interestRate": 0.05, "feeRate": 0.01, "maturityMonths": 24,
                                         "repaymentSource": "old_sales", "excel": {"cell": "B7"}}]})
    identity = feasibility_review_state(repo.project)["terms"]["financing"][0]["tranche_id"]
    apply(repo, {"financing": [tranche(tranche_id=identity, name="Reviewed name")]})
    row = repo.project["pf_structure"][0]
    assert all(row[key] is None for key in ("interestRate", "feeRate", "maturityMonths", "repaymentSource"))
    assert row["tranche"] == "Reviewed name"
    assert row["excel"] == {"cell": "B7"}
    actual = DevelopmentFeasibilityAgent()._financing_v2([row])[0]
    assert actual["interest_rate"] is None
    assert actual["fee_rate"] is None
    assert actual["term_months"] is None
    assert actual["repayment_source"] is None


@pytest.mark.parametrize("priority", [None, "not-classified"])
def test_unknown_legacy_priority_requires_explicit_reviewer_selection(priority):
    repo = Repository({"pf_structure": [{"name": "legacy", "priority": priority}]})
    dto = feasibility_review_state(repo.project)["terms"]["financing"][0]
    assert dto["priority"] == ""
    with pytest.raises(ValueError):
        apply(repo, {"financing": [{**dto, "interest_rate": 0}]})
    assert repo.updates == 0


def test_missing_terms_remain_null_without_inventing_rate_maturity_or_repayment():
    repo = Repository({"schedule": {"land_purchase": 0, "completion": "M24", "custom": "kept"}})
    state = feasibility_review_state(repo.project)
    assert state["terms"]["schedule"]["land_purchase"] == "M0"
    apply(repo, {"schedule": {"loan_maturity": "2029-01-01"}, "financing": [tranche()]})
    assert repo.project["schedule"]["completion"] == "M24"
    assert repo.project["schedule"]["land_purchase"] == 0
    assert repo.project["schedule"]["custom"] == "kept"
    assert all(repo.project["pf_structure"][0][key] is None for key in
               ("amount", "interest_rate", "fee_rate", "term_months", "repayment_source"))
    assert feasibility_review_state(repo.project)["terms"]["financing"] == [tranche()]


def test_schedule_clear_and_review_note_only_keep_metadata():
    repo = Repository({"schedule": {"completion": {"plannedDate": "2027-01-01", "excel": {"cell": "A9"}}, "custom": "kept"}})
    apply(repo, {"schedule": {"completion": None}})
    assert feasibility_review_state(repo.project)["terms"]["schedule"]["completion"] is None
    assert repo.project["schedule"]["completion"]["excel"] == {"cell": "A9"}
    apply(repo, {"schedule": {}}, reviews={"schedule": review(reason="출처 재확인")})
    assert feasibility_review_state(repo.project)["termReviews"]["schedule"]["reason"] == "출처 재확인"


@pytest.mark.parametrize("existing,changes", [
    ({"construction_start": "2028-01-01"}, {"completion": "2027-12-31"}),
    ({"completion": "2028-01-01"}, {"construction_start": "2028-01-02"}),
    ({}, {"construction_start": "2028-01-02", "completion": "2028-01-01"}),
])
def test_completion_before_construction_rejected_atomically(existing, changes):
    repo = Repository({"schedule": existing, "feasibility_input": {"total_revenue": 100}})
    before = deepcopy(repo.project)
    with pytest.raises(ValueError, match="준공일"):
        apply_feasibility_input_use_case(repo, "1", {"total_revenue": 999}, applied_by="reviewer",
                                         expected_review_hash=feasibility_review_state(before)["reviewHash"],
                                         terms={"schedule": changes}, term_reviews={"schedule": review()})
    assert repo.project == before
    assert repo.updates == 0


def test_same_construction_and_completion_date_allowed():
    repo = Repository({"id": "1"})
    apply(repo, {"schedule": {"construction_start": "2028-01-01", "completion": "2028-01-01"}})
    assert repo.updates == 1


def test_term_reviews_disappear_when_working_values_change_and_hash_covers_hidden_values():
    repo = Repository({"schedule": {"completion": "2027-01-01", "custom": "first"}})
    apply(repo, {"schedule": {}}, reviews={"schedule": review()})
    state = feasibility_review_state(repo.project)
    repo.project["schedule"]["custom"] = "changed elsewhere"
    with pytest.raises(FeasibilityReviewConflict):
        apply_feasibility_input_use_case(repo, "1", {}, applied_by="other", expected_review_hash=state["reviewHash"],
                                         terms={"schedule": {"completion": "2028-01-01"}}, term_reviews={"schedule": review()})
    repo.project["schedule"]["completion"] = "2029-01-01"
    assert "schedule" not in feasibility_review_state(repo.project)["termReviews"]
    assert repo.updates == 1


@pytest.mark.parametrize("terms", [
    {"unknown": {}}, {"schedule": {"unknown": "2027-01-01"}},
    {"schedule": {"completion": "2027-02-30"}}, {"schedule": {"completion": "M24"}},
    {"schedule": {"completion": True}}, {"schedule": []}, {"financing": {}},
    {"financing": [tranche(unknown=1)]}, {"financing": [tranche(), tranche()]},
    {"financing": [tranche(priority="unknown")]}, {"financing": [tranche(name=" ")]},
    {"financing": [tranche(amount=-1)]}, {"financing": [tranche(amount=True)]},
    {"financing": [tranche(amount=1.1)]}, {"financing": [tranche(amount=10**400)]},
    {"financing": [tranche(interest_rate=float("nan"))]},
    {"financing": [tranche(interest_rate=float("inf"))]},
    {"financing": [tranche(interest_rate=1.1)]}, {"financing": [tranche(fee_rate=-0.1)]},
    {"financing": [tranche(term_months=0)]}, {"financing": [tranche(term_months=1201)]},
    {"financing": [tranche(term_months=True)]}, {"financing": [tranche(term_months=1.2)]},
    {"financing": [tranche(repayment_source=False)]},
])
def test_invalid_terms_reject_money_and_terms_atomically(terms):
    repo = Repository({"feasibility_input": {"total_revenue": 123}, "schedule": {"completion": "2027-01-01"}})
    before = deepcopy(repo.project)
    with pytest.raises(ValueError):
        apply_feasibility_input_use_case(repo, "1", {"total_revenue": 999}, applied_by="reviewer",
                                         expected_review_hash=feasibility_review_state(before)["reviewHash"],
                                         terms=terms, term_reviews={key: review() for key in terms})
    assert repo.project == before
    assert repo.updates == 0


@pytest.mark.parametrize("review_data", [None, {}, {"origin": "user_assumption", "reason": ""},
                                             {"origin": [], "reason": "why"},
                                             review(origin="document_correction", sourceCell="A1")])
def test_terms_require_reason_hash_and_document_reference(review_data):
    repo = Repository({"id": "1"})
    with pytest.raises(ValueError):
        apply_feasibility_input_use_case(repo, "1", {}, applied_by="reviewer", terms={"schedule": {}},
                                         term_reviews={"schedule": review_data},
                                         expected_review_hash=feasibility_review_state(repo.project)["reviewHash"])
    with pytest.raises(ValueError):
        apply_feasibility_input_use_case(repo, "1", {}, applied_by="reviewer", terms={"schedule": {}}, term_reviews={"schedule": review()})
    assert repo.updates == 0


def test_api_terms_save_reopen_sources_and_conflict():
    client = TestClient(app)
    pid = client.post("/projects/", json={"project": {"name": "R1 terms"}, "schedule": {"custom": "kept"}}).json()["id"]
    try:
        state = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
        workbook = openpyxl.Workbook()
        workbook.active["A1"] = "2028-01-01"
        stream = BytesIO()
        workbook.save(stream)
        original = stream.getvalue()
        source = client.post(f"/projects/{pid}/feasibility-workbook/extract?manual_review=true",
                             files={"file": ("terms.xlsx", original, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}).json()["upload"]
        payload = {"feasibility_input": {"total_revenue": 0}, "expectedReviewHash": state["reviewHash"],
                   "fieldReviews": {"total_revenue": review()},
                   "terms": {"schedule": {"completion": "2028-01-01"}, "financing": [tranche(amount=0, interest_rate=0)]},
                   "termReviews": {"schedule": review(origin="document_correction", sourceCell="Sheet!A1"), "financing": review()},
                   "termSourceDocuments": {"schedule": {"archiveId": source["archiveId"], "originalFileName": "spoof.xlsx"}}}
        saved = client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json=payload)
        assert saved.status_code == 200, saved.text
        reopened = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
        assert reopened["terms"]["schedule"]["completion"] == "2028-01-01"
        assert reopened["terms"]["financing"][0]["interest_rate"] == 0
        assert reopened["terms"]["financing"][0]["term_months"] is None
        assert reopened["termReviews"]["schedule"]["sourceDocument"]["originalFileName"] == "terms.xlsx"
        assert get_project_record(pid)["schedule"]["custom"] == "kept"
        assert client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json=payload).status_code == 409
        payload["expectedReviewHash"] = reopened["reviewHash"]
        payload["termSourceDocuments"]["schedule"]["archiveId"] = "invalid-archive"
        assert client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json=payload).status_code == 400
        assert client.get(f"/projects/{pid}/feasibility-workbook/review").json() == reopened
        payload["termSourceDocuments"]["schedule"]["archiveId"] = source["archiveId"]
        payload["terms"]["financing"][0]["amount"] = -1
        assert client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json=payload).status_code == 400
        assert client.get(f"/projects/{pid}/feasibility-workbook/review").json() == reopened
        content = client.get(f"/projects/{pid}/source-documents/{source['archiveId']}/content")
        assert content.content == original
    finally:
        client.delete(f"/projects/{pid}")
