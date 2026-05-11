import json
from pathlib import Path

from fastapi.testclient import TestClient

from agents.legal_review_agent import LegalReviewAgent
from agents.marketability_agent import MarketabilityAgent
from main import app


def load_example(filename: str):
    path = Path(__file__).parents[1] / "data" / "examples" / filename
    return json.loads(path.read_text(encoding="utf-8"))


def test_marketability_agent_applies_to_saleable_mixed_use_project():
    result = MarketabilityAgent().analyze(load_example("daegu_bondong_mixed_use_pf.json"))

    assert result["applicable"] is True
    assert result["assetClass"] == "주상복합"
    assert result["score"] is not None
    assert result["checks"]


def test_marketability_agent_skips_non_saleable_logistics_project():
    result = MarketabilityAgent().analyze(load_example("dudong_lg_logistics_pf.json"))

    assert result["applicable"] is False
    assert result["grade"] == "N/A"


def test_legal_review_agent_flags_missing_korean_law_mcp_context():
    result = LegalReviewAgent().review(load_example("daegu_bondong_mixed_use_pf.json"))

    assert result["mcp"]["requested"] is True
    assert result["mcp"]["connected"] is False
    assert any(check["key"] == "korean_law_mcp" for check in result["checks"])
    assert result["disclaimer"]


def test_marketability_and_legal_review_endpoints():
    client = TestClient(app)
    project_id = next(
        item["project"]["id"]
        for item in client.get("/projects/").json()
        if item["project"]["type"] == "mixed_use"
    )

    marketability = client.post(f"/projects/{project_id}/marketability/analyze", json={}).json()
    legal = client.post(f"/projects/{project_id}/legal/review", json={}).json()

    assert marketability["applicable"] is True
    assert legal["reviewType"] == "korean_real_estate_pf_legal_precheck"
