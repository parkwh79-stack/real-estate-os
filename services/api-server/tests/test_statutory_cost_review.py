"""Evidence linkage and missing-amount behavior for statutory cost review."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from test_sangbong_financial_review import Repository, source
from use_cases.legal_intelligence import resolve_project_legal_context
from use_cases.statutory_cost_review import build_statutory_cost_review
from use_cases.workbook_import_approval import persist_feasibility_workbook_candidate_use_case

_SCHOOL = "학교용지 확보 등에 관한 특례법"
_ART = "문화예술진흥법"


def _law_reference(title=_SCHOOL, article="제5조"):
    return {
        "lawName": title,
        "sourceAuthority": "law_go_kr",
        "verified": True,
        "verificationStatus": "verified_snapshot",
        "resourceVersionId": "edition-1",
        "contentHash": "b" * 64,
        "article": article,
        "effectiveDate": "2025-01-01",
        "requestedAsOfDate": "2026-07-01",
        "url": "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=123",
    }


def _sangbong_case(profile, key, cell, amount):
    archived = source()
    draft = {
        "project_id": "p1",
        "raw_import": {"profile": profile, "sourceDocument": archived},
        "cost_items": [{
            "key": key, "label": "제세공과금", "category": "tax_and_dues", "unit": "KRW", "amount": amount,
            "provenance": {
                "source_type": "xlsx", "source_document": archived["originalFileName"],
                "sheet_name": "1. 사업수지", "cell": cell,
            },
        }],
    }
    repository = Repository({"id": "p1", "project": {"id": "p1"}})
    persist_feasibility_workbook_candidate_use_case(
        repository, "p1", {"feasibilityStudyV2Draft": draft, "sourceDocument": archived}, recorded_by="reviewer",
    )
    registration = repository.project["feasibilityStudyV2Draft"]["raw_import"]["candidateImport"]
    repository.project["source_documents"] = [{
        **archived, "documentType": "feasibility_workbook", "feasibilityStudyV2DraftHash": registration["draftHash"],
    }]
    return repository.project


@pytest.mark.parametrize(("profile", "key", "cell", "amount"), [
    ("sangbong-sale-feasibility", "cost_122", "K122", 750_000),
    ("sangbong-private-rental-feasibility", "cost_110", "K110", 0),
])
def test_sangbong_subtotal_requires_registered_exact_candidate(profile, key, cell, amount):
    project = _sangbong_case(profile, key, cell, amount)
    review = build_statutory_cost_review(project)
    subtotal = review["workbookSubtotal"]
    assert subtotal["amountKrw"] == amount
    assert subtotal["evidence"][0]["locator"]["cell"] == cell
    assert all(item["amountKrw"] is None for item in review["items"])
    project["feasibilityStudyV2Draft"]["cost_items"][0]["amount"] += 1000
    assert build_statutory_cost_review(project)["workbookSubtotal"] is None

    wrong_cell = _sangbong_case(profile, key, "K1", amount)
    assert build_statutory_cost_review(wrong_cell)["workbookSubtotal"] is None


def test_verified_law_and_sourced_market_facts_leave_applicability_unknown():
    project = {
        "project": {"type": "apartment"},
        "landLedger": {"parcels": [{"pnu": "1111010100100010000"}]},
        "statutoryCostFacts": {"school_site_charge": {"amountKrw": 500_000, "status": "paid"}},
        "legalPermitReview": {
            "statutoryCostAsOfDate": "2026-07-01",
            "statutoryCostLawReferences": [
                _law_reference(),
                {**_law_reference(_ART, "제9조"), "requestedAsOfDate": "2026-06-01"},
            ],
            "statutoryCostLookupStatus": {_SCHOOL: "reference_found", _ART: "unavailable"},
        },
        "feasibilityStudyV2": {"cost_items": [{
            "key": "levy_and_charges", "category": "tax_and_dues", "unit": "KRW", "amount": 2_000_000,
            "provenance": {
                "source_type": "xlsx", "source_document": "본리동 검증 모형.xlsx", "sheet_name": "사업수지", "cell": "D52",
            },
        }]},
    }
    market_review = {"evidenceRecords": [{
        "evidenceType": "product_mix", "extractedFields": {"units": 315, "totalRevenue": 38_000_000},
        "fieldSourceLocators": {"units": {"evidenceId": "housing-unit-schedule:p4", "pageNumber": 4}},
    }, {
        "evidenceType": "comparable", "extractedFields": {"units": 500},
        "fieldSourceLocators": {"units": {"evidenceId": "external-comparable:p1", "pageNumber": 1}},
    }]}
    original = deepcopy(project)
    school, art = build_statutory_cost_review(
        project, market_review=market_review, cost_review={"costBreakdown": {"totalCost": 200_000_000}},
    )["items"]

    assert project == original
    assert school["legalLookupStatus"] == "reference_found"
    assert art["legalLookupStatus"] == "unavailable"
    assert school["evidence"][0]["status"] == "verified_article"
    assert art["evidence"][0]["status"] == "reference_only"
    market = [evidence for evidence in school["evidence"] if evidence["sourceType"] == "market_product"]
    assert [evidence["value"] for evidence in market] == [315]
    assert market[0]["locator"]["pageNumber"] == 4
    assert school["status"] == art["status"] == "unknown"
    assert school["amountKrw"] is art["amountKrw"] is None
    assert school["amountKind"] is art["amountKind"] is None
    assert school["missingInputs"] and art["missingInputs"]


def test_statutory_lookup_is_read_only_public_and_does_not_expand_permit_gate():
    queries = []

    def search(**kwargs):
        queries.append(kwargs)
        return SimpleNamespace(authoritative_hits=[], candidate_hits=[], provider_trace=[], query_run_id=None)

    runtime = SimpleNamespace(search=search)
    project = {"project": {"name": "Confidential", "type": "housing"}}
    baseline = resolve_project_legal_context(project, service=runtime, max_queries=1, as_of=date(2026, 7, 1))
    optional = resolve_project_legal_context(
        project, service=runtime, max_queries=1, as_of=date(2026, 7, 1), include_statutory_costs=True,
    )
    assert baseline["legalIntelligence"]["requiredClaimCount"] == optional["legalIntelligence"]["requiredClaimCount"]
    assert baseline["legalIntelligence"]["citationVerification"] == optional["legalIntelligence"]["citationVerification"]
    assert {query["query"] for query in queries[-2:]} == {_SCHOOL, _ART}
    assert all(query["data_classification"].value == "public" for query in queries[-2:])
    assert optional["statutoryCostLookupStatus"] == {_SCHOOL: "unavailable", _ART: "unavailable"}
    assert optional["statutoryCostLawReferences"] == []


def test_optional_law_lookup_error_stays_in_cost_review_scope():
    def search(**kwargs):
        if kwargs["query"] == _SCHOOL:
            raise RuntimeError("provider disconnected")
        return SimpleNamespace(authoritative_hits=[], candidate_hits=[], provider_trace=[], query_run_id=None)

    resolution = resolve_project_legal_context(
        {}, service=SimpleNamespace(search=search), max_queries=1, include_statutory_costs=True,
    )
    assert resolution["legalIntelligence"]["requiredClaimCount"] == 1
    assert resolution["statutoryCostLookupStatus"][_SCHOOL] == "unavailable"


def test_preflight_carries_registered_workbook_into_cost_and_cashflow_reviews():
    from use_cases.chief_preflight_review import run_chief_preflight_review

    project = _sangbong_case("sangbong-sale-feasibility", "cost_122", "K122", 750_000)
    project["project"]["type"] = "housing"
    original = deepcopy(project)
    review = run_chief_preflight_review("p1", project, workflow_track="development", allow_public_lookups=False)
    by_key = {item["podKey"]: item for item in review["pods"]}
    for key in ("cost_schedule", "feasibility_cashflow"):
        statutory_review = by_key[key]["statutoryCostReview"]
        assert statutory_review["workbookSubtotal"]["amountKrw"] == 750_000
        assert statutory_review["items"][0]["amountKrw"] is None
    assert project == original
