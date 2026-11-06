from copy import deepcopy

import pytest

from agents.ai_connection import AIConnection
from agents.legal_review_agent import LegalReviewAgent
from use_cases import chief_preflight_review as review

PNU = "1111010100100010000"


def project():
    return {
        "project": {"id": "deal", "type": "apartment", "name": "부분 검토"},
        "source_documents": [{"archiveId": "source-1", "originalFileName": "사업수지.xlsx", "documentType": "feasibility"}],
        "land": {"pnu": PNU, "area_sqm": 1000},
        "landEvidence": {"records": [{"pnu": PNU, "evidenceType": "cadastral", "sourcePnuVerified": True}]},
        "feasibility_input": {"total_revenue": 100_000_000, "total_cost": 70_000_000, "construction_cost": 40_000_000},
        "schedule": {"construction_months": 12},
        "pf_structure": [{"amount": 30_000_000, "interest_rate": 0.05}],
    }


def offline(source, **kwargs):
    return review.run_chief_preflight_review("deal", source, allow_public_lookups=False, **kwargs)


def record_pods(monkeypatch):
    called = []
    execute = review._execute_agents

    def wrapper(project_id, pod_key, source, **kwargs):
        called.append(pod_key)
        return execute(project_id, pod_key, source, **kwargs)

    monkeypatch.setattr(review, "_execute_agents", wrapper)
    return called


def test_partial_source_runs_actual_agents_without_llm_or_writes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No provider, approval, or durable runtime may be called")
    monkeypatch.setattr(AIConnection, "extract_json", forbidden)
    monkeypatch.setattr(AIConnection, "embed_text", forbidden)
    from agent_platform.runtime import DurableAgentRuntime
    monkeypatch.setattr(DurableAgentRuntime, "execute_sync", forbidden)
    from use_cases import legal_pod
    monkeypatch.setattr(legal_pod, "upsert_legal_review_record", forbidden)
    monkeypatch.setattr(legal_pod, "upsert_land_evidence_record", forbidden)
    source = {"project": {"type": "apartment"}, "source_documents": project()["source_documents"]}
    before = deepcopy(source)
    result = offline(source)
    assert source == before
    assert result["reviewLoop"]["executedCount"] == 6
    assert result["reviewLoop"]["materialRequestsFinal"] is True
    agents = {row["agentId"] for row in result["reviewLoop"]["agents"]}
    assert {"LegalReviewAgent", "MarketEvidenceAgent", "MarketabilityAgent", "MarketProductDepthAgent",
            "DevelopmentFeasibilityAgent", "RiskRegisterAgent", "CreditReviewAgent",
            "DevelopabilityScoreAgent", "PodAIReviewerAgent"} <= agents
    assert result["analysisOnly"] is True
    assert result["reviewLoop"]["status"] == "needs_material"
    assert {row["itemId"] for row in result["materialRequests"]} >= {"cost_assumption_missing", "revenue_assumption_missing"}
    state = result["resumeState"]["pods"]
    assert state["cost_schedule"]["status"] == "needs_material"
    assert "feasibility_input" not in state["cost_schedule"]["patch"]
    assert "cashFlowRunResult" not in state["feasibility_cashflow"]["patch"]
    assert state["public_policy"]["result"]["unsupportedTasks"]


def test_same_source_reuses_all_actual_agent_results(monkeypatch):
    calls = record_pods(monkeypatch)
    source = project()
    first = offline(source)
    calls.clear()
    second = offline(source, previous_state=first["resumeState"])
    assert calls == []
    assert second["reviewLoop"]["executedCount"] == 0
    assert second["reviewLoop"]["reusedCount"] == 6
    assert second["reviewLoop"]["totalExecutedCount"] == 6
    assert second["reviewLoop"]["canContinue"] is False


def test_cost_edit_replays_only_cost_cashflow_and_credit_chain(monkeypatch):
    calls = record_pods(monkeypatch)
    source = project()
    first = offline(source)
    calls.clear()
    source["feasibility_input"]["total_cost"] += 1_000_000
    second = offline(source, previous_state=first["resumeState"])
    assert calls == ["cost_schedule", "feasibility_cashflow", "pf_credit"]
    assert second["reviewLoop"]["executedCount"] == 3
    assert second["reviewLoop"]["reusedCount"] == 3


def test_failure_needs_explicit_retry_and_does_not_skip_other_agents(monkeypatch):
    real = review.MarketEvidenceAgent.review
    failing = True
    calls = []

    def maybe_fail(self, source):
        calls.append("evidence")
        if failing:
            raise TypeError("programming error must not be labelled missing material")
        return real(self, source)

    monkeypatch.setattr(review.MarketEvidenceAgent, "review", maybe_fail)
    first = offline(project())
    assert first["reviewLoop"]["status"] == "retry_needed"
    assert first["resumeState"]["pods"]["market_product"]["status"] == "failed"
    assert any(row["agentId"] == "MarketabilityAgent" and row["status"] in {"completed", "needs_material"}
               for row in first["reviewLoop"]["agents"])
    second = offline(project(), previous_state=first["resumeState"])
    assert calls == ["evidence"]
    assert second["reviewLoop"]["executedCount"] == 0
    failing = False
    third = offline(project(), previous_state=second["resumeState"], retry_failed=True)
    assert calls == ["evidence", "evidence"]
    assert third["reviewLoop"]["status"] != "retry_needed"
    assert third["reviewLoop"]["executedCount"] <= 3
    assert all(row["podKey"] not in {"land_legal", "cost_schedule", "public_policy"}
               for row in third["reviewLoop"]["agents"] if not row["reused"])


def test_time_budget_continuation_keeps_cumulative_work_bound(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(review, "monotonic", lambda: clock["now"])
    execute = review._execute_agents

    def advances(*args, **kwargs):
        output = execute(*args, **kwargs)
        clock["now"] += 1
        return output

    monkeypatch.setattr(review, "_execute_agents", advances)
    first = offline(project(), time_budget_seconds=2.1)
    assert first["reviewLoop"]["status"] == "limit_reached"
    assert first["reviewLoop"]["stopReason"] == "time_budget"
    assert first["reviewLoop"]["canContinue"] is True
    assert first["reviewLoop"]["materialRequestsFinal"] is False
    second = offline(project(), previous_state=first["resumeState"], time_budget_seconds=2.1)
    assert second["reviewLoop"]["executedCount"] == 3
    assert second["reviewLoop"]["totalExecutedCount"] == 6
    assert second["reviewLoop"]["totalRounds"] == 1
    assert second["reviewLoop"]["canContinue"] is False


def test_execution_cap_does_not_reset_on_another_request():
    first = offline(project(), max_executions=2)
    assert first["reviewLoop"]["stopReason"] == "execution_limit"
    assert first["reviewLoop"]["totalExecutedCount"] == 2
    assert first["reviewLoop"]["canContinue"] is False
    second = offline(project(), previous_state=first["resumeState"], max_executions=2)
    assert second["reviewLoop"]["executedCount"] == 0
    assert second["reviewLoop"]["pendingPodKeys"] == first["reviewLoop"]["pendingPodKeys"]


def test_cyclic_requests_with_no_new_semantic_input_are_not_executed_twice(monkeypatch):
    execute = review._execute_agents
    def cycle(*args, **kwargs):
        output = execute(*args, **kwargs)
        output["workOrders"] = [{"podKey": "land_legal", "action": "rerun_pod"}]
        return output
    monkeypatch.setattr(review, "_execute_agents", cycle)
    result = offline(project())
    assert result["reviewLoop"]["executedCount"] == 6
    assert result["reviewLoop"]["pendingPodKeys"] == []
    assert result["reviewLoop"]["canContinue"] is False


def test_semantic_revision_ignores_runtime_but_preserves_source_dates_and_versions():
    source = project()
    before = review.source_revision(source, "development")
    same = deepcopy(source)
    same["workflowTrack"] = "development"
    same["_meta"] = {"stateVersion": 99, "updatedAt": "tomorrow"}
    same["chief_orchestration_state"] = {"podRuns": {"market_product": {"status": "completed"}}}
    same["marketabilityAnalysis"] = {"score": 99}
    assert review.source_revision(same, "development") == before
    source["pf_structure"][0]["maturity_date"] = "2030-01-01"
    assert review.source_revision(source, "development") != before
    source["inputVersion"] = 2
    changed = review.source_revision(source, "development")
    source["inputVersion"] = 3
    assert review.source_revision(source, "development") != changed


def test_bound_workbook_draft_is_reviewed_without_promoting_candidate_numbers():
    source = {"project": project()["project"], "source_documents": project()["source_documents"],
              "feasibilityStudyV2Draft": {"metrics": {"total_revenue": 1_000_000_000, "total_cost": 500_000_000},
                                         "raw_import": {"sourceDocument": {"archiveId": "source-1"}}}}
    result = offline(source)
    requests = {row["itemId"]: row for row in result["materialRequests"]}
    for item_id in ("cost_assumption_missing", "revenue_assumption_missing"):
        assert requests[item_id]["action"] == "review_document"
        assert requests[item_id]["sourceLocators"][0]["archiveId"] == "source-1"
    assert "cashFlowRunResult" not in result["resumeState"]["pods"]["feasibility_cashflow"]["patch"]
    other = deepcopy(source)
    other["feasibilityStudyV2Draft"]["raw_import"]["sourceDocument"]["archiveId"] = "other-project"
    unbound = offline(other)
    assert next(row for row in unbound["materialRequests"] if row["itemId"] == "cost_assumption_missing")["action"] == "provide_material"


def test_public_rows_reach_actual_market_agents_before_requests_are_collected(monkeypatch):
    source = project()
    looked_up = []
    monkeypatch.setattr(review, "_confirmed_parcels", lambda *args, **kwargs: (
        {PNU}, {"key": "parcels", "label": "VWorld", "status": "checked", "message": "matched"},
    ))
    class Connector:
        def __init__(self, **kwargs):
            pass
        def configured(self):
            return True
    monkeypatch.setattr(review, "MolitRtmsConnector", Connector)
    def fetch(*args, **kwargs):
        looked_up.append(kwargs)
        return [{"source": "molit_rtms", "pricePerPy": 50_000_000, "dealKind": "trade", "address": "공식 사례"}], {"status": "ok"}
    monkeypatch.setattr(review, "fetch_project_market_comparables", fetch)
    def legal_preview(project_id, source, options, *, persist):
        assert persist is False and options["autoEnrichLand"] is False
        return {"legalReview": LegalReviewAgent().review(source), "buildableEnvelope": None}
    monkeypatch.setattr(review, "run_legal_pod_use_case", legal_preview)
    seen = []
    execute = review._execute_agents
    def spy(project_id, key, current, **kwargs):
        seen.append((key, deepcopy(current)))
        return execute(project_id, key, current, **kwargs)
    monkeypatch.setattr(review, "_execute_agents", spy)
    first = review.run_chief_preflight_review("deal", source)
    market = dict(seen)["market_product"]
    assert len(market["market"]["transactionComparables"]) == 1
    credit = dict(seen)["pf_credit"]
    assert credit["marketabilityAnalysis"]["normalizedComparables"][0]["sourceName"] == "molit_rtms"
    assert "legalPermitReview" in dict(seen)["feasibility_cashflow"]
    assert len(looked_up) == 1
    assert looked_up[0]["lawd_cd"] == "11110" and looked_up[0]["max_records"] == 12
    assert "market_evidence_missing" not in {row["itemId"] for row in first["materialRequests"]}
    assert "transactionComparables" not in source.get("market", {})
    seen.clear()
    second = review.run_chief_preflight_review("deal", source, previous_state=first["resumeState"])
    assert len(looked_up) == 1
    assert second["reviewLoop"]["executedCount"] == 0
    assert second["resumeState"]["publicState"]["patch"]["market"]["transactionComparables"]


def test_public_failure_is_a_connection_retry_not_a_final_document_request(monkeypatch):
    monkeypatch.setattr(review, "_confirmed_parcels", lambda *args, **kwargs: (
        set(), {"key": "parcels", "label": "VWorld", "status": "failed", "message": "timeout"},
    ))
    monkeypatch.setattr(review, "run_legal_pod_use_case", lambda project_id, source, options, persist:
                        {"legalReview": LegalReviewAgent().review(source)})
    result = review.run_chief_preflight_review("deal", project())
    assert result["reviewLoop"]["status"] == "retry_needed"
    assert result["reviewLoop"]["materialRequestsFinal"] is False
    assert result["reviewLoop"]["failedPodKeys"] == ["land_legal"]




def test_explicit_failed_retries_stop_at_cumulative_round_budget(monkeypatch):
    def fail(self, source):
        raise TypeError("a real implementation failure")
    monkeypatch.setattr(review.MarketEvidenceAgent, "review", fail)
    result = offline(project())
    for _ in range(2):
        result = offline(project(), previous_state=result["resumeState"], retry_failed=True)
    assert result["reviewLoop"]["totalRounds"] == 3
    assert result["reviewLoop"]["canRetryFailed"] is False
    capped = offline(project(), previous_state=result["resumeState"], retry_failed=True)
    assert capped["reviewLoop"]["executedCount"] == 0
    assert capped["reviewLoop"]["stopReason"] == "round_limit"
    assert capped["reviewLoop"]["canContinue"] is False



def test_verified_parcel_batches_are_reused_before_market_lookup(monkeypatch):
    from use_cases import chief_pod_preflight as checks
    source = project()
    source["land"] = {"area_sqm": 1000}
    source["landEvidence"] = {"records": []}
    source["landLedger"] = {"parcels": [
        {"pnu": f"11110101001000{lot}0000", "lotNumber": str(lot)} for lot in (1, 2, 3)
    ]}
    parcels = []
    monkeypatch.setattr(checks, "vworld_configured", lambda: True)
    def parcel(pnu, *, timeout):
        parcels.append(pnu)
        return {"pnu": pnu}, {"status": "resolved"}
    monkeypatch.setattr(checks, "fetch_parcel_by_pnu", parcel)
    class Connector:
        def __init__(self, **kwargs):
            pass
        def configured(self):
            return True
    monkeypatch.setattr(review, "MolitRtmsConnector", Connector)
    trades = []
    def market(*args, **kwargs):
        trades.append(kwargs)
        return [{"source": "molit_rtms", "pricePerPy": 50_000_000}], {"status": "ok"}
    monkeypatch.setattr(review, "fetch_project_market_comparables", market)
    monkeypatch.setattr(review, "run_legal_pod_use_case", lambda project_id, current, options, persist:
                        {"legalReview": LegalReviewAgent().review(current)})
    result = review.run_chief_preflight_review("deal", source)
    assert len(parcels) == len(set(parcels)) == 3
    assert len(trades) == 1
    assert len(result["resumeState"]["publicState"]["patch"]["landEvidence"]["records"]) == 3
    assert result["reviewLoop"]["pendingPodKeys"] == []


def test_zero_progress_time_budget_never_autocontinues():
    result = offline(project(), time_budget_seconds=0)
    assert result["reviewLoop"]["executedCount"] == 0
    assert result["reviewLoop"]["stopReason"] == "time_budget"
    assert result["reviewLoop"]["canContinue"] is False


def _no_market_connection(monkeypatch):
    class Connector:
        def __init__(self, **kwargs):
            pass

        def configured(self):
            return False

    monkeypatch.setattr(review, "MolitRtmsConnector", Connector)


@pytest.mark.parametrize("method", ["address_geocode", "pnu_construction"])
def test_saved_pnu_identification_never_becomes_cadastral_evidence(monkeypatch, method):
    from use_cases import chief_pod_preflight as checks
    source = project()
    actual_locator = {"sourceType": "api_row", "sourceName": "VWorld 주소 검색", "apiRowId": PNU}
    record = {
        "pnu": PNU, "evidenceType": "pnu_resolution", "verificationStatus": "resolved",
        "matchMethod": method, "sourceLocator": actual_locator,
    }
    source["landEvidence"] = {"records": [record]}
    before = deepcopy(source)
    _no_market_connection(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("A saved PNU identification must not invent a new public lookup")

    monkeypatch.setattr(checks, "fetch_parcel_by_pnu", forbidden)
    state = review._collect_public("deal", source, None, retry_failed=False, deadline=review.monotonic() + 10)
    assert state["lookups"][0]["status"] == "available"
    assert "landEvidence" not in state["patch"]
    confirmed, _ = checks._confirmed_parcels("deal", source, allow_live=False)
    assert confirmed == {PNU}
    assert checks._pnu_source_locators(source, confirmed, []) == [actual_locator]
    assert source == before


@pytest.mark.parametrize("response_pnu", [PNU, "1111010100100020000"])
def test_only_actual_matching_cadastral_response_creates_cadastral_record(monkeypatch, response_pnu):
    from use_cases import chief_pod_preflight as checks
    source = project()
    source["landEvidence"] = {"records": []}
    before = deepcopy(source)
    monkeypatch.setattr(checks, "vworld_configured", lambda: True)
    monkeypatch.setattr(checks, "fetch_parcel_by_pnu", lambda pnu, timeout: (
        {"pnu": response_pnu, "addr": "서울특별시 종로구 청운동 1", "jibun": "1"},
        {"status": "resolved"},
    ))
    _no_market_connection(monkeypatch)
    state = review._collect_public("deal", source, None, retry_failed=False, deadline=review.monotonic() + 10)
    records = state["patch"].get("landEvidence", {}).get("records", [])
    if response_pnu == PNU:
        assert len(records) == 1
        record = records[0]
        assert record["pnu"] == response_pnu and record["sourcePnuVerified"] is True
        assert record["evidenceType"] == "cadastral"
        assert record["cadastralAddress"] == "서울특별시 종로구 청운동 1"
        assert record["lotNumber"] == "1"
        assert record["sourceName"] == "VWorld cadastral properties"
        assert "sourceUrl" not in record["sourceLocator"]
        assert record["sourceLocator"]["apiRowId"] == response_pnu
        assert record["sourceLocator"]["reference"] == f"vworld:{PNU}:{checks.CADASTRAL_LAYER}"
        assert not {"geometry", "boundary", "zoning"} & record.keys()
    else:
        assert records == []
        assert state["lookups"][0]["status"] == "failed"
    assert source == before


@pytest.mark.parametrize("confirmed_parcel", [False, True])
def test_unconfigured_public_lookup_is_visible_limitation_and_allows_remaining_review(monkeypatch, confirmed_parcel):
    from use_cases import chief_pod_preflight as checks
    source = project()
    source["landEvidence"] = {"records": []}
    if confirmed_parcel:
        source["landEvidence"]["records"] = [{
            "pnu": PNU, "evidenceType": "pnu_resolution", "verificationStatus": "resolved",
            "matchMethod": "address_geocode", "sourceLocator": {"sourceName": "VWorld 주소 검색"},
        }]
    source["feasibility_input"].pop("total_cost")
    source["feasibility_input"].pop("construction_cost")
    monkeypatch.setattr(checks, "vworld_configured", lambda: False)
    _no_market_connection(monkeypatch)
    monkeypatch.setattr(review, "run_legal_pod_use_case", lambda project_id, current, options, persist:
                        {"legalReview": LegalReviewAgent().review(current)})
    before = deepcopy(source)
    result = review.run_chief_preflight_review("deal", source)
    lookups = {row["key"]: row for row in result["publicLookups"]}
    assert lookups["market" if confirmed_parcel else "parcels"]["status"] == "unavailable"
    assert result["reviewLoop"]["executedCount"] == 6
    assert result["reviewLoop"]["status"] == "needs_material"
    assert result["reviewLoop"]["failedPodKeys"] == []
    assert result["reviewLoop"]["publicLookupFailures"] == []
    assert result["reviewLoop"]["materialRequestsFinal"] is True
    assert result["reviewLoop"]["canRetryFailed"] is False
    assert "cost_assumption_missing" in {row["itemId"] for row in result["materialRequests"]}
    assert source == before


def test_previous_runner_version_cannot_reuse_old_public_provenance():
    source = project()
    first = offline(source)
    previous = deepcopy(first["resumeState"])
    previous["runnerVersion"] = "realytics-chief-preflight-review-v1"
    previous["publicState"] = {"patch": {"landEvidence": {"records": [{
        "pnu": PNU, "evidenceType": "cadastral", "sourceName": "old synthetic record",
    }]}}}
    result = offline(source, previous_state=previous)
    assert result["reviewLoop"]["executedCount"] == 6
    assert result["reviewLoop"]["reusedCount"] == 0
    assert result["resumeState"]["publicState"] is None
    assert result["reviewLoop"]["runnerVersion"] == "realytics-chief-preflight-review-v2"


def test_pnu_identification_does_not_borrow_unverified_same_pnu_document_locator():
    from use_cases import chief_pod_preflight as checks
    source = project()
    source["land"] = {
        "pnu": PNU, "pnuMatchMethod": "address_geocode",
        "pnuResolution": {"lotVerification": "verified", "source": "VWorld address API"},
    }
    source["landEvidence"] = {"records": [{
        "pnu": PNU, "evidenceType": "registry", "sourcePnuVerified": False,
        "sourceLocator": {"sourceName": "미확인 등기 문서", "archiveId": "unverified-source"},
    }]}
    confirmed, _ = checks._confirmed_parcels("deal", source, allow_live=False)
    assert confirmed == {PNU}
    assert checks._pnu_source_locators(source, confirmed, []) == [{
        "sourceType": "pnu_resolution", "sourceName": "VWorld address API", "reference": PNU,
    }]
