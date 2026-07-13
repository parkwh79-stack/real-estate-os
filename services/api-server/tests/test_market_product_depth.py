from fastapi.testclient import TestClient

from agents.market_product_depth_agent import MarketProductDepthAgent
from agents.pod_ai_reviewer_agent import _market_product_findings
from main import app
from routers import projects


def _isolate(tmp_path, monkeypatch, name):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / name))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(projects, "persist_state", lambda: None)


def _project_with_sources():
    return {
        "project": {"name": "시장성 QA", "type": "apartment"},
        "source_documents": [
            {"documentType": "사업계획서", "originalFileName": "본리동_사업계획서.pdf"},
            {"documentType": "매각제안서", "originalFileName": "매각주관사_분양대행_제안.pdf"},
        ],
        "documentParsedMarketFacts": {"proposedPricePerPy": 70_000_000},
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
    assert prov["developerProvided"] >= 1  # 사업계획서 → 시행사
    assert prov["salesAgentProvided"] >= 1  # 매각제안서 → 매각 주관사
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
        options={"podAiReviewMode": "deterministic"},
    )
    ab = depth["absorptionModel"]
    assert ab["rateBasis"] == "planning_benchmark"
    assert ab["monthlyAbsorptionRate"] == 0.06
    assert ab["estimatedMonthsToSellout"]
    assert ab["curve"] and ab["curve"][-1]["cumulativeRate"] <= 1.0


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
    project_id = client.post("/projects/", json=_project_with_sources()).json()["project"]["id"]
    res = client.post(
        f"/projects/{project_id}/market-product-depth/analyze",
        json={"options": {"podAiReviewMode": "deterministic"}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["schemaVersion"] == "realytics-market-product-depth-v1"
    assert body["sourceProvenance"]["salesAgentProvided"] >= 1
    client.delete(f"/projects/{project_id}")
