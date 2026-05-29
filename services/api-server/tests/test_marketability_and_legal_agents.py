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
    assert result["exitSaleRate"]["discount20"] == result["exitSaleRate"]["stress"]
    assert len(result["exitSaleRate"]["discountScenarios"]) == 5
    assert len(result["exitSaleRate"]["trancheThresholds"]) == 2
    assert result["absorptionForecast"]["exitPressure"]["pressureLevel"] in {"low", "moderate", "high"}
    assert result["discountTrigger"][1]["triggerType"] == "soft_discount"
    assert result["unsoldRecovery"]["haircut"] == 0.15
    assert result["unsoldRecovery"]["trancheRecovery"][0]["recoveryRatio"] == 1


def test_marketability_agent_distinguishes_missing_price_basis_from_missing_comparables():
    result = MarketabilityAgent().analyze({
        "project": {"type": "officetel"},
        "product_mix": [
            {
                "type": "A",
                "contractAreaPy": 20,
                "units": 100,
            }
        ],
        "pf_structure": [{"name": "Tr.A", "amount": 35_000_000_000, "priority": 1}],
        "sales_schedule": {"contract_deposit": 0.1},
        "market": {
            "comparables": [
                {"name": "Nearby A", "pricePerPy": 30_000_000, "sourceUrlOrEvidenceId": "comp-a"},
                {"name": "Nearby B", "pricePerPy": 31_000_000, "sourceUrlOrEvidenceId": "comp-b"},
            ],
        },
    })

    assert result["comparableSummary"]["totalCount"] == 2
    assert result["pricing"]["judgement"] == "needs_proposed_price_basis"
    assert result["pricing"]["hasComparableData"] is True
    assert result["pricing"]["proposedPriceBasisStatus"] == "missing"
    assert "PROPOSED_PRICE_BASIS_MISSING" in result["riskFlags"]
    assert "NO_COMPARABLE_DATA" not in result["riskFlags"]
    assert "proposed_price_per_py_basis" in result["missingRequiredData"]
    detail = next(flag for flag in result["riskFlagDetails"] if flag["code"] == "PROPOSED_PRICE_BASIS_MISSING")
    assert detail["sourceLocators"]
    assert any(locator["path"] == "marketabilityAnalysis.pricing.proposedPricePerPy" for locator in detail["sourceLocators"])


def test_marketability_agent_derives_price_basis_from_product_mix_revenue():
    result = MarketabilityAgent().analyze({
        "project": {"type": "officetel"},
        "product_mix": [
            {
                "type": "A",
                "contractAreaPy": 20,
                "units": 100,
                "totalRevenue": 60_000_000_000,
            }
        ],
        "pf_structure": [{"name": "Tr.A", "amount": 35_000_000_000, "priority": 1}],
        "sales_schedule": {"contract_deposit": 0.1},
        "market": {
            "comparables": [
                {"name": "Nearby A", "pricePerPy": 30_000_000, "sourceUrlOrEvidenceId": "comp-a"},
                {"name": "Nearby B", "pricePerPy": 31_000_000, "sourceUrlOrEvidenceId": "comp-b"},
            ],
        },
    })

    assert result["productMix"][0]["pricePerPy"] == 30_000_000
    assert result["pricing"]["proposedPricePerPy"] == 30_000_000
    assert result["pricing"]["proposedPriceBasis"] == "product_mix_weighted_average"
    assert result["pricing"]["proposedPriceBasisStatus"] == "available"
    assert "PROPOSED_PRICE_BASIS_MISSING" not in result["riskFlags"]


def test_marketability_agent_accepts_manual_transaction_presale_and_listing_arrays():
    result = MarketabilityAgent().analyze({
        "project": {"type": "mixed_use"},
        "product_mix": [
            {
                "type": "A",
                "contractAreaPy": 30,
                "units": 10,
                "pricePerPy": 32_000_000,
            }
        ],
        "feasibility_input": {"total_revenue": 9_600_000_000},
        "pf_structure": [{"amount": 5_000_000_000, "priority": 1}],
        "sales_schedule": {"contract_deposit": 0.1},
        "market": {
            "comparables": [{"name": "Manual A", "pricePerPy": 30_000_000, "sourceUrlOrEvidenceId": "manual-1"}],
            "transactionComparables": [{
                "name": "Tx A",
                "pricePerSqm": 9_200_000,
                "sourceUrlOrEvidenceId": "tx-1",
                "sourceName": "MOLIT",
                "sourceType": "public_api",
                "collectedAt": "2026-05-18T00:00:00Z",
                "baseDate": "2026-05-01",
                "licenseOrUsageNote": "Official public API",
                "confidence": 0.92,
            }],
            "presaleComparables": [{"name": "Presale A", "presalePricePerPy": 31_000_000, "sourceUrlOrEvidenceId": "presale-1"}],
            "listingComparables": [{"name": "Listing A", "listingPricePerPy": 33_000_000, "sourceUrlOrEvidenceId": "listing-1"}],
        },
    })

    assert result["comparableSummary"]["totalCount"] == 4
    assert result["comparableSummary"]["hasManualComparable"] is True
    assert result["comparableSummary"]["hasTransactionComparable"] is True
    assert result["comparableSummary"]["hasPresaleComparable"] is True
    assert result["comparableSummary"]["hasListingComparable"] is True
    assert {item["comparableCategory"] for item in result["normalizedComparables"]} == {
        "manual_comparable",
        "transaction",
        "presale",
        "listing",
    }
    assert any(item["priceBasis"] == "price_per_sqm_converted" for item in result["normalizedComparables"])
    tx_case = next(item for item in result["normalizedComparables"] if item["comparableCategory"] == "transaction")
    assert tx_case["sourceMetadata"]["metadataStatus"] == "complete"
    assert result["sourceMetadataSummary"]["completeSourceCount"] == 1
    assert result["sourceMetadataSummary"]["partialSourceCount"] == 3
    assert "licenseOrUsageNote" in result["sourceMetadataSummary"]["missingFieldCounts"]


def test_marketability_agent_calculates_exit_absorption_trigger_and_unsold_recovery():
    result = MarketabilityAgent().analyze({
        "project": {"type": "commercial"},
        "product_mix": [
            {
                "type": "retail",
                "contractAreaPy": 100,
                "units": 10,
                "pricePerPy": 25_000_000,
            }
        ],
        "feasibility_input": {"total_revenue": 25_000_000_000},
        "pf_structure": [
            {"tranche": "Senior", "amount": 12_000_000_000, "priority": 1},
            {"tranche": "Mezz", "amount": 5_000_000_000, "priority": 2},
            {"tranche": "Junior", "amount": 2_000_000_000, "priority": 3},
        ],
        "sales_schedule": {"contract_deposit": 0.1},
        "market": {
            "demandLevel": "weak",
            "upcomingSupplyUnits": 1200,
            "comparables": [
                {"name": "Retail A", "pricePerPy": 24_000_000, "sourceUrlOrEvidenceId": "retail-a"},
                {"name": "Retail B", "pricePerPy": 23_000_000, "sourceUrlOrEvidenceId": "retail-b"},
            ],
        },
    })

    assert result["exitSaleRate"]["base"] == 0.76
    assert round(result["exitSaleRate"]["discount10"], 4) == 0.8444
    assert result["exitSaleRate"]["trancheExitSaleRate"]["senior"] == 0.48
    assert result["exitSaleRate"]["trancheThresholds"][2]["mappedName"] == "junior"
    assert result["absorptionForecast"]["baseCase"]["exitCoverage"] is False
    assert result["absorptionForecast"]["exitPressure"]["pressureLevel"] == "high"
    assert result["discountTrigger"][-1]["targetSalesRate"] >= result["exitSaleRate"]["discount10"]
    assert result["unsoldRecovery"]["haircut"] == 0.25
    assert result["unsoldRecovery"]["recoveryScenarios"][2]["scenario"] == "stress_unsold_recovery"
    assert result["unsoldRecovery"]["trancheRecovery"][1]["recoveryRatio"] <= 1
    assert result["scoreBreakdown"]["model"] == "marketability_score_v2"
    assert result["scoreBreakdown"]["weightProfile"]["pfExit"] == 13
    assert "WEAK_DEMAND" in result["riskFlags"]
    assert "ABSORPTION_BELOW_EXIT" in result["riskFlags"]
    assert "UNSOLD_LTV_HIGH" in result["riskFlags"]
    assert any(flag["code"] == "MEZZ_RECOVERY_WEAK" for flag in result["riskFlagDetails"])
    assert "Forecast completion sales rate" in result["reportParagraphs"]["businessFeasibility"]
    assert "Unsold LTV" in result["reportParagraphs"]["unsoldRisk"]


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
                    "sourceLocator": {
                        "pageNumber": 12,
                        "table": "sales price table",
                        "row": "A",
                        "column": "price per py",
                        "fieldName": "pricePerPy",
                        "textExcerpt": "A type price per py 36,000,000",
                        "confidence": 0.82,
                    },
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
                    "sourceUrlOrEvidenceId": "doc-p20-table1-row2",
                    "baseDate": "2026-05-14",
                    "collectedAt": "2026-05-18T00:00:00Z",
                    "licenseOrUsageNote": "Uploaded business plan evidence",
                    "confidence": 0.75,
                    "sourceLocator": {
                        "page": 20,
                        "tableIndex": 1,
                        "rowIndex": 2,
                        "field": "pricePerPy",
                        "excerpt": "Document Comparable A 30,000,000 per py",
                    },
                }
            ],
            "rentComparables": [
                {
                    "caseId": "DOC-RENT-001",
                    "name": "Rent Comparable A",
                    "monthlyRentPerPy": 120_000,
                    "sourceUrlOrEvidenceId": "doc-p21-table2-row1",
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
    assert evidence["evidenceSummary"]["evidenceRecordCount"] == 3
    assert evidence["evidenceSummary"]["sourceMetadataCompleteCount"] >= 1
    assert evidence["evidenceSummary"]["sourceMetadataPartialCount"] >= 1
    assert evidence["evidenceSummary"]["sourceLocatorCount"] == 3
    assert evidence["evidenceSummary"]["hasPageLevelLocators"] is True
    assert evidence["evidenceSummary"]["fieldLocatorCoverage"]["status"] == "complete"
    assert evidence["evidenceSummary"]["fieldLocatorCoverage"]["coveredFieldCount"] > 0
    assert {record["evidenceType"] for record in evidence["evidenceRecords"]} == {
        "product_mix",
        "sale_comparable",
        "rent_comparable",
    }
    assert all(record["sourceLocator"]["pageNumber"] for record in evidence["evidenceRecords"])
    assert evidence["evidenceRecords"][0]["sourceLocator"]["textExcerpt"] == "A type price per py 36,000,000"
    assert evidence["evidenceRecords"][0]["fieldSourceLocators"]["pricePerPy"]["pageNumber"] == 12
    assert evidence["evidenceRecords"][1]["fieldSourceLocators"]["pricePerPy"]["fieldName"] == "pricePerPy"
    assert evidence["evidenceRecords"][1]["sourceMetadata"]["metadataStatus"] == "complete"
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


def test_market_evidence_agent_merges_document_and_existing_market_payloads():
    project_data = {
        "project": {"type": "officetel"},
        "product_mix": [
            {
                "type": "A",
                "contractAreaPy": 20,
                "units": 100,
                "pricePerPy": 30_000_000,
                "totalRevenue": 60_000_000_000,
            }
        ],
        "feasibility_input": {"total_revenue": 60_000_000_000},
        "market": {
            "comparables": [
                {
                    "caseId": "MANUAL-001",
                    "pricePerPy": 29_000_000,
                    "sourceUrlOrEvidenceId": "manual-001",
                }
            ],
        },
        "document_market_evidence": {
            "comparables": [
                {
                    "caseId": "DOC-001",
                    "pricePerPy": 30_000_000,
                    "sourceUrlOrEvidenceId": "doc-p10-table1-row1",
                }
            ],
            "transactionComparables": [
                {
                    "caseId": "TX-001",
                    "pricePerPy": 31_000_000,
                    "sourceUrlOrEvidenceId": "tx-001",
                }
            ],
        },
    }

    evidence = MarketEvidenceAgent().review(project_data)

    assert len(evidence["marketPayloadPatch"]["market"]["comparables"]) == 2
    assert len(evidence["marketPayloadPatch"]["market"]["transactionComparables"]) == 1
    assert evidence["evidenceSummary"]["saleComparableCount"] == 3
    assert len([record for record in evidence["evidenceRecords"] if record["evidenceType"] == "sale_comparable"]) == 3


def test_market_evidence_agent_adds_field_locator_coverage_from_evidence_ids():
    project_data = {
        "project": {"type": "officetel"},
        "feasibility_input": {"total_revenue": 6_000_000_000},
        "document_market_evidence": {
            "productMix": [
                {
                    "type": "A",
                    "contractAreaPy": 20,
                    "units": 10,
                    "pricePerPy": 30_000_000,
                    "totalRevenue": 6_000_000_000,
                    "evidenceIds": ["doc-p10-table1-row2"],
                }
            ],
            "comparables": [
                {
                    "caseId": "DOC-COMP-002",
                    "name": "Document Comparable B",
                    "pricePerPy": 29_000_000,
                    "sourceUrlOrEvidenceId": "doc-p11-table2-row3",
                }
            ],
        },
    }

    evidence = MarketEvidenceAgent().review(project_data)
    product_record = next(record for record in evidence["evidenceRecords"] if record["evidenceType"] == "product_mix")
    comparable_record = next(record for record in evidence["evidenceRecords"] if record["evidenceType"] == "sale_comparable")

    assert product_record["locatorCoverage"]["status"] == "complete"
    assert product_record["fieldSourceLocators"]["pricePerPy"]["pageNumber"] == 10
    assert product_record["fieldSourceLocators"]["pricePerPy"]["locatorStatus"] == "record_level"
    assert comparable_record["fieldSourceLocators"]["pricePerPy"]["pageNumber"] == 11
    assert evidence["evidenceSummary"]["fieldLocatorCoverage"]["status"] == "complete"


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
