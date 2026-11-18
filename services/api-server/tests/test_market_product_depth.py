from fastapi.testclient import TestClient
import pytest

from agents.market_product_depth_agent import MarketProductDepthAgent
from agents.pod_ai_reviewer_agent import _market_product_findings
from main import app
from routers import projects
from use_cases import market_product_depth


def _isolate(tmp_path, monkeypatch, name):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / name))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(projects, "persist_state", lambda: None)


def _project_with_sources():
    return {
        "project": {"name": "시장성 QA", "type": "apartment"},
        "source_documents": [
            {"documentType": "사업계획서", "originalFileName": "본리동_사업계획서.pdf", "dataProvider": "developer"},
            {"documentType": "매각제안서", "originalFileName": "매각주관사_분양대행_제안.pdf", "dataProvider": "sales_agent"},
        ],
        "documentParsedMarketFacts": {"proposedPricePerPy": 70_000_000, "dataProvider": "developer"},
        "market": {
            "transactionComparables": [
                {"source": "molit_rtms", "sourceType": "public_api", "pricePerPy": 60_000_000, "address": "인근 A"},
                {"source": "molit_rtms", "sourceType": "public_api", "pricePerPy": 62_000_000, "address": "인근 B"},
            ],
        },
        "product_mix": [{"productType": "apartment", "pricePerPy": 70_000_000}],
    }


def test_provider_classification_developer_and_sales_agent():
    depth = MarketProductDepthAgent().analyze(_project_with_sources(), {}, options={"podAiReviewMode": "deterministic"})
    prov = depth["sourceProvenance"]
    assert prov["developerProvided"] == 1
    assert prov["salesAgentProvided"] == 1
    assert prov["publicData"] >= 2  # 국토부 실거래가 2건
    assert "developer" in prov["byProvider"] and prov["byProvider"]["developer"]["label"] == "시행사 제공 자료"


def test_provider_comparison_anchors_on_public_median():
    depth = MarketProductDepthAgent().analyze(_project_with_sources(), {}, options={"podAiReviewMode": "deterministic"})
    comparison = depth["providerComparison"]
    assert comparison["publicMedianPerPy"] == 61_000_000  # median(60M, 62M)
    dev = next(r for r in comparison["rows"] if r["provider"] == "developer")
    assert dev["status"] == "submitted"
    # 70M vs 61M median → ~+14.8% higher
    assert dev["deltaVsPublicMedian"] and dev["deltaVsPublicMedian"] > 0.1


def test_absorption_model_uses_benchmark_when_no_forecast():
    depth = MarketProductDepthAgent().analyze(
        {"project": {"type": "apartment"}, "architectural_plan": {"total_units": 100}}, {},
        options={"podAiReviewMode": "deterministic", "allowPlanningBenchmark": True},
    )
    ab = depth["absorptionModel"]
    assert ab["rateBasis"] == "planning_benchmark"
    assert ab["status"] == "planning_only"
    assert ab["monthlyAbsorptionRate"] == 0.06
    assert ab["estimatedMonthsToSellout"]
    assert ab["curve"] and ab["curve"][-1]["cumulativeRate"] <= 1.0


def test_three_percent_absorption_reaches_sellout_in_month_34():
    result = MarketProductDepthAgent()._absorption_model(
        {"project": {"type": "commercial"}, "architectural_plan": {"total_units": 100}}, {}, allow_benchmark=True
    )
    assert result["estimatedMonthsToSellout"] == 34
    assert result["curve"][-1] == {"month": 34, "cumulativeRate": 1.0}


@pytest.mark.parametrize("rate", [-0.1, 1.1, float("inf"), float("nan"), "bad"])
def test_invalid_explicit_absorption_is_not_replaced_with_benchmark(rate):
    result = MarketProductDepthAgent()._absorption_model({}, {"absorptionForecast": {"monthlyAbsorptionRate": rate}})
    assert result["status"] == "invalid_input"
    assert result["estimatedMonthsToSellout"] is None
    assert result["curve"] == []


def test_zero_absorption_has_no_sellout():
    result = MarketProductDepthAgent()._absorption_model({}, {"absorptionForecast": {"monthlyAbsorptionRate": 0}})
    assert result["monthlyAbsorptionRate"] == 0
    assert result["estimatedMonthsToSellout"] is None


def test_ai_next_actions_deterministic_mode_lists_gaps():
    # No sales agent + no public data → deterministic next actions flag both.
    thin = {"project": {"name": "Thin", "type": "apartment"}}
    depth = MarketProductDepthAgent().analyze(thin, {}, options={"podAiReviewMode": "deterministic"})
    na = depth["aiRecommendedNextActions"]
    assert na["mode"] == "deterministic"
    actions_text = " ".join(a["action"] for a in na["actions"])
    assert "매각 주관사" in actions_text
    assert "실거래가" in actions_text


def test_market_findings_flag_missing_sales_agent_and_overvaluation():
    artifact = {
        "marketProductDepth": {
            "sourceProvenance": {"salesAgentProvided": 0, "developerProvided": 1, "publicData": 2},
            "providerComparison": {"rows": [{"provider": "developer", "label": "시행사 제공 자료", "deltaVsPublicMedian": 0.2}]},
        },
    }
    findings = _market_product_findings(artifact, "art-market", {}, {})
    msgs = " ".join(f["message"] for f in findings)
    assert "매각 주관사" in msgs
    assert "고평가" in msgs


def test_market_product_depth_endpoint(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, "market-depth.sqlite3")
    client = TestClient(app)
    project_data = _project_with_sources()
    source_documents = project_data.pop("source_documents")
    project_id = client.post("/projects/", json=project_data).json()["project"]["id"]
    for index, source_document in enumerate(source_documents):
        projects.source_document_repository().save_upload_metadata(
            project_id,
            {**source_document, "archiveId": f"market-depth-fixture-{index}"},
        )
    res = client.post(
        f"/projects/{project_id}/market-product-depth/analyze",
        json={"options": {"podAiReviewMode": "deterministic"}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["schemaVersion"] == "realytics-market-product-depth-v1"
    assert body["sourceProvenance"]["salesAgentProvided"] >= 1
    client.delete(f"/projects/{project_id}")



def test_unattributed_archive_is_visible_without_fake_forecast_or_public_anchor():
    depth = MarketProductDepthAgent().analyze(
        {
            "project": {"type": "apartment"},
            "architectural_plan": {"total_units": 100},
            "source_documents": [{"archiveId": "doc-1", "originalFileName": "매각주관사_제안서.pdf"}],
            "documentParsedMarketFacts": {"proposedPricePerPy": 70_000_000},
            "market": {"transactionComparables": [{"sourceType": "public_api", "pricePerPy": 62_000_000}]},
            "marketabilityAnalysis": {"absorptionForecast": {"monthlyAbsorptionRate": 0.05}},
        },
        {},
        options={"podAiReviewMode": "deterministic"},
    )
    provenance = depth["sourceProvenance"]
    assert provenance["archivedDocumentCount"] == 1
    assert provenance["byProvider"]["unspecified"]["items"][0]["archiveId"] == "doc-1"
    assert provenance["salesAgentProvided"] == provenance["publicData"] == 0
    assert depth["providerComparison"]["publicMedianPerPy"] is None
    assert all(row["proposedPricePerPy"] is None for row in depth["providerComparison"]["rows"])
    assert depth["absorptionModel"]["status"] == "insufficient_data"
    assert depth["absorptionModel"]["monthlyAbsorptionRate"] is None
    assert depth["absorptionModel"]["estimatedMonthsToSellout"] is None


def test_price_requires_located_review_and_named_archived_provider():
    proposed = {
        "canonicalField": "market.proposedPricePerPy",
        "normalizedValue": 70_000_000,
        "sourceDocument": {"archiveId": "doc-1"},
        "sourceLocator": {"page": 3},
        "locatorStatus": "located",
        "validationStatus": "pod_validated",
    }
    project = {
        "source_documents": [{"archiveId": "doc-1", "dataProvider": "developer"}],
        "parsedFacts": [
            proposed,
            {**proposed, "normalizedValue": 60_000_000, "validationStatus": "unverified"},
            {**proposed, "normalizedValue": 50_000_000, "sourceDocument": {"archiveId": "other-project"}},
        ],
    }
    agent = MarketProductDepthAgent()
    assert agent._provider_proposed_prices(project) == {"developer": 70_000_000, "sales_agent": None}
    project["parsedFacts"].append({**proposed, "normalizedValue": 80_000_000})
    assert agent._provider_proposed_prices(project) == {"developer": None, "sales_agent": None}


def test_display_lookup_requires_one_resolved_district_and_known_asset(monkeypatch):
    class DisplayConnector:
        def configured(self):
            return True

        def fetch(self, **options):
            calls.append(options)
            return [
                {"dealKind": "trade", "pricePerPy": 60_000_000, "address": "test 1"},
                {"dealKind": "trade", "pricePerPy": 64_000_000, "address": "test 2"},
            ], {"status": "ok"}

    calls = []
    monkeypatch.setattr(market_product_depth, "molit_display_connector", DisplayConnector)
    monkeypatch.setattr(market_product_depth, "recent_deal_yms", lambda n: ["202609"])
    parcel = {"pnu": "1111010100100010000"}
    project = {"project": {"type": "apartment"}, "land": parcel}
    result = market_product_depth.project_market_public_lookup(project)
    assert result == {
        "status": "available", "count": 2, "sourceName": "국토교통부 실거래가",
        "lawdCd": "11110", "assetType": "apartment", "month": "202609", "medianPerPy": 62_000_000,
    }
    assert calls == [{"lawd_cd": "11110", "deal_ym": "202609", "asset_type": "apartment", "deal_kind": "trade", "num_rows": 30}]

    monkeypatch.setattr(market_product_depth, "canonical_project_parcel_rows", lambda source: [
        parcel, {"pnu": "1114010100100010000"},
    ])
    assert market_product_depth.project_market_public_lookup(project)["status"] == "needs_input"
    assert len(calls) == 1


def test_marketability_live_lookup_remains_separate_and_rejects_save(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, "market-display.sqlite3")
    calls = []

    def lookup(project):
        calls.append(project["project"]["id"])
        return {"status": "available", "count": 1, "sourceName": "official", "medianPerPy": 62_000_000}

    monkeypatch.setattr(market_product_depth, "project_market_public_lookup", lookup)
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "market read", "type": "apartment"}})
    project_id = created.json()["project"]["id"]
    path = f"/projects/{project_id}/marketability/analyze"
    baseline = client.post(path, json={})
    before = client.get(f"/projects/{project_id}").json()
    live = client.post(path, params={"live_comparables": True}, json={})
    assert live.status_code == 200
    assert live.json()["publicLookup"]["medianPerPy"] == 62_000_000
    assert {key: value for key, value in live.json().items() if key != "publicLookup"} == baseline.json()
    rejected = client.post(path, params={"live_comparables": True}, json={"persist": True})
    assert rejected.status_code == 400
    assert client.get(f"/projects/{project_id}").json() == before
    assert calls == [project_id]
    client.delete(f"/projects/{project_id}")



def test_bucket_type_does_not_identify_a_public_or_partner_provider():
    depth = MarketProductDepthAgent().analyze(
        {"market": {
            "transactionComparables": [{"sourceType": "public_api", "pricePerPy": 50_000_000}],
            "listingComparables": [{"sourceType": "partner_api", "pricePerPy": 55_000_000}],
        }},
        {},
        options={"podAiReviewMode": "deterministic"},
    )
    assert depth["sourceProvenance"]["publicData"] == 0
    assert depth["sourceProvenance"]["partnerProvided"] == 0
    assert depth["sourceProvenance"]["unspecified"] == 2
