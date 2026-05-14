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
    assert result["analysisType"] == "marketability_analysis_v2"
    assert result["productType"] == "mixed_use_residential"
    assert result["assetClass"] == "주상복합"
    assert result["score"] is not None
    assert result["checks"]
    assert result["exitSaleRate"]["base"] > 0
    assert result["reportParagraphs"]["businessFeasibility"]
    assert "market_data_source_metadata" in result["missingRequiredData"]


def test_marketability_agent_skips_non_saleable_logistics_project():
    result = MarketabilityAgent().analyze(load_example("dudong_lg_logistics_pf.json"))

    assert result["applicable"] is False
    assert result["productType"] == "non_saleable"
    assert result["grade"] == "N/A"


def test_marketability_agent_treats_presale_serviced_residence_as_applicable():
    result = MarketabilityAgent().analyze({
        "project": {"type": "serviced_residence"},
        "land": {"address": "서울특별시 중구 인현동"},
        "feasibility_input": {"total_revenue": 100_000_000_000},
        "pf_structure": [{"amount": 55_000_000_000, "priority": 1}],
        "sales_schedule": {"contract_deposit": 0.1, "middle_payment": 0.6, "balance": 0.3},
        "market": {
            "price_reasonableness": "인근 생숙 및 오피스텔 비교 필요",
            "data_sources": [{
                "sourceName": "manual comparable fixture",
                "sourceType": "manual_upload",
                "sourceUrlOrEvidenceId": "fixture-market-001",
                "baseDate": "2026-05-14",
                "confidence": 0.7,
            }],
        },
    })

    assert result["applicable"] is True
    assert result["productType"] == "serviced_residence"
    assert result["assetClass"] == "생활형숙박시설"
    assert result["dataSources"][0]["sourceName"] == "manual comparable fixture"


def test_legal_review_agent_flags_missing_korean_law_mcp_context():
    result = LegalReviewAgent().review(load_example("daegu_bondong_mixed_use_pf.json"))

    assert result["mcp"]["requested"] is True
    assert result["mcp"]["connected"] is False
    assert any(check["key"] == "korean_law_mcp" for check in result["checks"])
    assert result["disclaimer"]
    assert result["recommendedMcpQueries"]


def test_legal_review_agent_normalizes_korean_law_mcp_context():
    law_context = {
        "source": "korean-law-mcp",
        "tool_results": [
            {
                "tool": "search_law",
                "result": {
                    "laws": [
                        {
                            "법령명한글": "건축법",
                            "조문번호": "제11조",
                            "시행일자": "20260512",
                            "summary": "건축허가 관련 조문",
                        }
                    ]
                },
            },
            {
                "tool": "verify_citations",
                "result": {
                    "references": [
                        {
                            "title": "국토의 계획 및 이용에 관한 법률",
                            "article": "제76조",
                            "verified": True,
                        }
                    ],
                    "citationVerification": {"status": "passed", "checkedCount": 2},
                },
            },
        ],
    }

    result = LegalReviewAgent().review(load_example("daegu_bondong_mixed_use_pf.json"), law_context)

    assert result["mcp"]["connected"] is True
    assert result["mcp"]["adapter"] == "realytics-law-context-v1"
    assert len(result["lawReferences"]) == 2
    assert any(reference["lawName"] == "건축법" for reference in result["lawReferences"])
    assert result["citationVerification"]["status"] == "passed"
    assert any(check["key"] == "korean_law_mcp" and check["severity"] == "clear" for check in result["checks"])


def test_legal_review_agent_blocks_on_citation_failures():
    law_context = {
        "source": "korean-law-mcp",
        "references": [{"title": "형법", "article": "제9999조"}],
        "citationVerification": {
            "status": "failed",
            "failures": ["형법 제9999조 does not exist"],
        },
    }

    result = LegalReviewAgent().review(load_example("daegu_bondong_mixed_use_pf.json"), law_context)

    assert result["status"] == "blocking_issue"
    assert any(check["key"] == "korean_law_mcp" and check["severity"] == "blocking" for check in result["checks"])


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
