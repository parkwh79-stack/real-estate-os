from copy import deepcopy
from time import monotonic

import pytest

from integrations.land_data_connectors import LandDataConnectorRouter
from use_cases import chief_preflight_review as review
from use_cases import chief_public_collection as collection
from use_cases.parcel_pnu_resolution import canonical_project_parcel_rows, parcel_pnu

PNU = "1111010100100010000"


def source():
    return {
        "project": {"id": "collect-deal", "type": "apartment"},
        "source_documents": [{"archiveId": "workbook", "originalFileName": "사업수지.xlsx"}],
        "landLedgerSources": {"a": [{"pnu": PNU, "areaSqm": 1000}]},
        "landEvidence": {"records": [{"pnu": PNU, "evidenceType": "cadastral", "sourcePnuVerified": True}]},
        "feasibility_input": {"total_cost": 7000, "total_revenue": 10000, "construction_cost": 4000},
        "schedule": {"construction_months": 12},
        "pf_structure": [{"amount": 3000, "interest_rate": 0.05}],
    }


def response(pnu):
    return {"landUsePlanningRecords": [{"pnu": pnu, "sourcePnuVerified": True,
                                         "zoning": "제2종일반주거지역", "legalFar": 200, "legalBcr": 60,
                                         "sourceName": "공공 원문", "sourceUrlOrEvidenceId": "public:zone"}],
            "officialLandPrices": [{"pnu": pnu, "sourcePnuVerified": True, "pricePerSqm": 100,
                                    "sourceUrlOrEvidenceId": "public:price"}],
            "landCharacteristicsRecords": [{"pnu": pnu, "sourcePnuVerified": True, "areaSqm": 1000,
                                              "sourceUrlOrEvidenceId": "public:characteristics"}]}


def mock_public(monkeypatch, fetch):
    monkeypatch.setattr(LandDataConnectorRouter, "fetch_records_for_parcels", fetch)
    monkeypatch.setattr(review, "_confirmed_parcels", lambda pid, data, **kw: (
        {parcel_pnu(row) for row in canonical_project_parcel_rows(data)},
        {"key": "parcels", "label": "필지", "status": "available", "message": "대조 완료"},
    ))
    monkeypatch.setattr(review, "fetch_project_market_comparables", lambda *a, **kw: ([], {"status": "empty"}))


def test_all_document_parcels_reach_real_legal_and_downstream_agents_without_writes(monkeypatch):
    data = source()
    for lot in (2, 3):
        pnu = f"11110101001000{lot}0000"
        data["landLedgerSources"][str(lot)] = [{"pnu": pnu, "areaSqm": 1000}]
        data["landEvidence"]["records"].append({"pnu": pnu, "sourcePnuVerified": True, "evidenceType": "cadastral"})
    before = deepcopy(data)
    calls = []
    def fetch(self, parcels, **options):
        assert options["evidence_types"] == set(collection.LAND_RESULTS)
        pnu = parcels[0]["pnu"]
        calls.append(pnu)
        return response(pnu)
    mock_public(monkeypatch, fetch)
    from use_cases import legal_pod
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview cannot persist public responses or approvals")
    monkeypatch.setattr(legal_pod, "upsert_land_evidence_record", forbidden)
    monkeypatch.setattr(legal_pod, "upsert_legal_review_record", forbidden)
    first = review.run_chief_preflight_review("collect-deal", data)
    assert len(calls) == len(set(calls)) == 3
    assert data == before
    pods = first["resumeState"]["pods"]
    legal = pods["land_legal"]["result"]["legalPermitReview"]
    assert {row["pnu"] for row in legal["parcelLegalReviews"]} == set(calls)
    assert legal["developabilityAnalysis"]["zoning"] == "제2종일반주거지역"
    assert any(row["field"] == "법정 용적률" and row["value"] == 200
               for row in legal["developabilityAnalysis"]["areaLawTable"])
    assert len(first["resumeState"]["publicState"]["patch"]["landEvidence"]["records"]) == 12
    assert "regulation_data_missing" not in {row["itemId"] for row in first["materialRequests"]}
    assert "registry_evidence_missing" in {row["itemId"] for row in first["materialRequests"]}
    assert len(first["reviewLoop"]["agents"]) >= 18
    calls.clear()
    second = review.run_chief_preflight_review("collect-deal", data, previous_state=first["resumeState"])
    assert calls == [] and second["reviewLoop"]["executedCount"] == 0


@pytest.mark.parametrize("wrong_pnu,verified", [("1111010100100020000", True), (PNU, False)])
def test_mismatched_or_unverified_public_record_is_rejected_and_explicitly_retried(monkeypatch, wrong_pnu, verified):
    calls = []
    def fetch(self, parcels, **options):
        calls.append(options)
        result = response(wrong_pnu)
        for key in collection.LAND_RESULTS.values():
            result[key[0]][0]["sourcePnuVerified"] = verified
        return result
    monkeypatch.setattr(LandDataConnectorRouter, "fetch_records_for_parcels", fetch)
    data = source()
    lookups, tasks = collection.collect_land_evidence(data, {PNU}, {}, deadline=monotonic() + 10, retry_failed=False)
    assert all(row["status"] == "failed" for row in lookups)
    assert len(data["landEvidence"]["records"]) == 1
    collection.collect_land_evidence(data, {PNU}, tasks, deadline=monotonic() + 10, retry_failed=False)
    assert len(calls) == 1
    collection.collect_land_evidence(data, {PNU}, tasks, deadline=monotonic() + 10, retry_failed=True)
    assert len(calls) == 2


def test_time_budget_defers_unqueried_land_and_resume_fetches_it(monkeypatch):
    monkeypatch.setattr(LandDataConnectorRouter, "fetch_records_for_parcels", lambda self, parcels, **kw: response(parcels[0]["pnu"]))
    data = source()
    lookups, tasks = collection.collect_land_evidence(data, {PNU}, {}, deadline=0, retry_failed=False)
    assert {row["status"] for row in lookups} == {"deferred"}
    lookups, _ = collection.collect_land_evidence(data, {PNU}, tasks, deadline=monotonic() + 10, retry_failed=False)
    assert {row["status"] for row in lookups} == {"checked"}


def test_no_connection_is_not_reported_as_completed_collection(monkeypatch):
    def fetch(self, parcels, **options):
        return {"diagnostics": [{"connector": kind, "status": "skipped_missing_api_key"} for kind in collection.LAND_RESULTS]}
    mock_public(monkeypatch, fetch)
    result = review.run_chief_preflight_review("collect-deal", source())
    orders = [row for row in result["resumeState"]["workOrders"] if row.get("missingItemId") == "regulation_data_missing"]
    assert orders and {row["status"] for row in orders} == {"unavailable"}
    assert "regulation_data_missing" in {row["itemId"] for row in result["materialRequests"]}


def test_new_public_evidence_changes_consumed_hash_and_reruns_following_pods(monkeypatch):
    failed = True
    def fetch(self, parcels, **options):
        if failed:
            raise TimeoutError("offline")
        return response(parcels[0]["pnu"])
    mock_public(monkeypatch, fetch)
    data = source()
    first = review.run_chief_preflight_review("collect-deal", data)
    assert first["reviewLoop"]["status"] == "retry_needed"
    failed = False
    second = review.run_chief_preflight_review("collect-deal", data, previous_state=first["resumeState"], retry_failed=True)
    assert second["reviewLoop"]["failedPodKeys"] == []
    for pod in ("land_legal", "market_product", "pf_credit", "public_policy"):
        assert second["resumeState"]["pods"][pod]["inputHash"] != first["resumeState"]["pods"][pod]["inputHash"]
    assert second["reviewLoop"]["executedCount"] >= 4
    assert {row["status"] for row in second["resumeState"]["workOrders"]
            if row.get("missingItemId") == "regulation_data_missing"} <= {"checked_in_preview"}


def test_reviewed_financial_source_is_retained_only_for_matching_working_value():
    data = source()
    data["_meta"] = {"feasibilityFieldReviews": {
        "total_cost": {"value": 7000, "origin": "document_correction", "sourceCell": "사업수지!C10",
                       "sourceDocument": {"archiveId": "workbook"}},
        "total_revenue": {"value": 99999, "origin": "document_correction", "sourceCell": "사업수지!C8",
                          "sourceDocument": {"archiveId": "workbook"}},
    }}
    result = review.run_chief_preflight_review("collect-deal", data, allow_public_lookups=False)
    for pod in ("cost_schedule", "feasibility_cashflow", "pf_credit"):
        actual = result["resumeState"]["pods"][pod]["result"]["inputSources"]
        assert [row["field"] for row in actual] == ["total_cost"]
        assert actual[0]["sourceLocator"]["cell"] == "사업수지!C10"
    assert not result.get("approvedInputSnapshot")


def test_adding_another_ledger_document_invalidates_parcel_consumers():
    data = source()
    first = review.run_chief_preflight_review("collect-deal", data, allow_public_lookups=False)
    data["landLedgerSources"]["b"] = [{"pnu": "1111010100100020000"}]
    second = review.run_chief_preflight_review("collect-deal", data, allow_public_lookups=False, previous_state=first["resumeState"])
    assert second["inputRevision"] != first["inputRevision"]
    assert second["resumeState"]["pods"]["land_legal"]["inputHash"] != first["resumeState"]["pods"]["land_legal"]["inputHash"]


def test_explicit_site_address_creates_only_transient_parcel_input(monkeypatch):
    data = source()
    data.pop("landLedgerSources")
    data["land"] = {"address": "서울특별시 종로구 청운동 1"}
    before = deepcopy(data)
    seen = []
    def confirmed(pid, working, **kwargs):
        rows = canonical_project_parcel_rows(working)
        seen.extend(deepcopy(rows))
        rows[0]["pnu"] = PNU
        working["landLedger"]["parcels"][0]["pnu"] = PNU
        return {PNU}, {"key": "parcels", "label": "필지", "status": "checked", "message": "verified"}
    mock_public(monkeypatch, lambda self, parcels, **kw: response(PNU))
    monkeypatch.setattr(review, "_confirmed_parcels", confirmed)
    result = review.run_chief_preflight_review("collect-deal", data)
    assert seen[0]["addressFragment"] == data["land"]["address"]
    assert result["resumeState"]["publicState"]["patch"]["landLedger"]["parcels"][0]["pnu"] == PNU
    assert data == before
