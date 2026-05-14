import json
from pathlib import Path

from fastapi.testclient import TestClient

from agents.legal_review_agent import LegalReviewAgent
from agents.market_evidence_agent import MarketEvidenceAgent
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
    assert result["assetClass"] == "Mixed-use Residential"
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
    assert result["assetClass"] == "Serviced Residence"
    assert result["dataSources"][0]["sourceName"] == "manual comparable fixture"


def test_marketability_agent_normalizes_product_mix_and_pricing_comparables():
    result = MarketabilityAgent().analyze({
        "project": {"type": "officetel"},
        "land": {"address": "Seoul Jung-gu sample site"},
        "product_mix": [
            {
                "type": "A",
                "exclusiveAreaPy": 12,
                "contractAreaPy": 20,
                "units": 100,
                "pricePerPy": 36_000_000,
                "evidenceIds": ["product-mix-001"],
            }
        ],
        "feasibility_input": {"total_revenue": 72_000_000_000},
        "pf_structure": [
            {"name": "Tr.A", "amount": 35_000_000_000, "priority": 1},
            {"name": "Tr.B", "amount": 10_000_000_000, "priority": 2},
        ],
        "sales_schedule": {"contract_deposit": 0.1, "middle_payment": 0.6, "balance": 0.3},
        "market": {
            "demandLevel": "moderate",
            "upcomingSupplyUnits": 500,
            "comparables": [
                {
                    "caseId": "COMP-001",
                    "name": "Nearby Officetel A",
                    "productType": "officetel_residential",
                    "distanceM": 200,
                    "pricePerPy": 30_000_000,
                    "sourceName": "manual fixture",
                    "sourceUrlOrEvidenceId": "market-comp-001",
                    "baseDate": "2026-05-14",
                    "confidence": 0.8,
                },
                {
                    "caseId": "COMP-002",
                    "name": "Nearby Officetel B",
                    "productType": "officetel_residential",
                    "distanceM": 400,
                    "pricePerPy": 31_000_000,
                    "sourceName": "manual fixture",
                    "sourceUrlOrEvidenceId": "market-comp-002",
                    "baseDate": "2026-05-14",
                    "confidence": 0.8,
                },
                {
                    "caseId": "COMP-003",
                    "name": "Nearby Officetel C",
                    "productType": "officetel_residential",
                    "distanceM": 600,
                    "pricePerPy": 32_000_000,
                    "sourceName": "manual fixture",
                    "sourceUrlOrEvidenceId": "market-comp-003",
                    "baseDate": "2026-05-14",
                    "confidence": 0.8,
                },
            ],
        },
    })

    assert result["productType"] == "officetel_residential"
    assert result["productMix"][0]["totalRevenue"] == 72_000_000_000
    assert len(result["normalizedComparables"]) == 3
    assert result["pricing"]["judgement"] == "high"
    assert "PRICE_PREMIUM_HIGH" in result["riskFlags"]
    assert result["reportParagraphs"]["pricingAnalysis"]


def test_market_evidence_agent_reconciles_uploaded_document_market_values():
    project_data = {
        "project": {"type": "officetel"},
        "sales_assumptions": {"avg_price_per_sqm": 9_075_000},
        "feasibility_input": {"total_revenue": 72_000_000_000},
        "document_market_evidence": {
            "productMix": [
                {
                    "type": "A",
                    "productType": "officetel_residential",
                    "exclusiveAreaPy": 12,
                    "contractAreaPy": 20,
                    "units": 100,
                    "pricePerPy": 36_000_000,
                    "totalRevenue": 72_000_000_000,
                    "evidenceIds": ["doc-p12-table3"],
                }
            ],
            "comparables": [
                {
                    "caseId": "DOC-COMP-001",
                    "name": "Document Comparable A",
                    "productType": "officetel_residential",
                    "distanceM": 250,
                    "pricePerPy": 30_000_000,
                    "sourceName": "uploaded business plan",
                    "sourceType": "manual_upload",
                    "sourceUrlOrEvidenceId": "doc-p20",
                    "baseDate": "2026-05-14",
                    "confidence": 0.75,
                }
            ],
            "rentComparables": [
                {
                    "caseId": "DOC-RENT-001",
                    "name": "Rent Comparable A",
                    "monthlyRentPerPy": 120_000,
                    "sourceUrlOrEvidenceId": "doc-p21",
                }
            ],
        },
        "market": {
            "rentalAssumptions": {"monthlyRentPerPy": 122_000},
        },
    }

    evidence = MarketEvidenceAgent().review(project_data)

    assert evidence["status"] == "needs_review"
    assert evidence["marketPayloadPatch"]["product_mix"][0]["type"] == "A"
    assert evidence["evidenceSummary"]["saleComparableCount"] == 1
    assert any(issue["code"] == "DOCUMENT_COMPARABLE_PRICE_PREMIUM" for issue in evidence["issues"])

    marketability_input = {
        **project_data,
        **evidence["marketPayloadPatch"],
        "pf_structure": [{"amount": 35_000_000_000, "priority": 1}],
        "sales_schedule": {"contract_deposit": 0.1},
        "marketEvidenceReview": evidence,
    }
    result = MarketabilityAgent().analyze(marketability_input)

    assert "MARKET_EVIDENCE_NEEDS_REVIEW" in result["riskFlags"]


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
