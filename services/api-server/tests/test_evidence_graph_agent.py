from agents.evidence_graph_agent import EvidenceGraphAgent


def test_evidence_graph_agent_links_report_sections_claims_and_evidence():
    project_data = {
        "project": {"id": "p-1", "name": "Evidence QA"},
        "_meta": {
            "upload": {
                "archiveId": "archive-1",
                "originalFileName": "business-plan.pdf",
                "originalFileHash": "hash-1",
            }
        },
    }
    run = {
        "project_id": "p-1",
        "run_id": "run-1",
        "handoffs": [
            {
                "handoffId": "h-1",
                "handoffType": "marketability_to_feasibility",
                "fromAgent": "MarketabilityAgent",
                "toAgent": "DevelopmentFeasibilityAgent",
                "evidenceIds": ["comp-1"],
                "payload": {},
            }
        ],
        "artifacts": {
            "marketabilityAnalysis": {
                "analysisType": "marketability_analysis_v2",
                "score": 82,
                "grade": "B",
                "pricing": {"judgement": "reasonable", "evidenceIds": ["comp-1"]},
            },
            "riskRegister": {
                "analysisType": "risk_register",
                "status": "high_risk",
                "risks": [
                    {
                        "riskId": "R-MKT-001",
                        "sourceAgent": "MarketabilityAgent",
                        "sourceArtifact": "marketabilityAnalysis",
                        "category": "Market Risk",
                        "code": "PRICE_PREMIUM_HIGH",
                        "title": "Price premium is high",
                        "description": "Pricing risk generated from comparable evidence.",
                        "severity": "high",
                        "probability": "medium",
                        "grade": "High",
                        "score": 12,
                        "evidenceIds": ["comp-1"],
                        "sourceDocumentIds": ["archive-1"],
                        "sourceLocators": [
                            {
                                "locatorId": "market-sheet:B12",
                                "sourceType": "excel_cell",
                                "source": "Market sheet",
                                "path": "B12",
                                "label": "Comparable median price",
                                "value": 32000000,
                            }
                        ],
                    }
                ],
            },
            "phase11Report": {
                "report": {
                    "sectionOrder": [{"key": "marketAnalysis", "title": "Market Analysis"}],
                    "sections": {"marketAnalysis": {"summary": "Market is acceptable."}},
                    "qualityChecks": {"isComplete": True},
                }
            },
        },
    }

    graph = EvidenceGraphAgent().build(project_data, run)

    assert graph["schemaVersion"] == "realytics-evidence-graph-v1"
    assert graph["summary"]["sourceDocumentCount"] == 1
    assert graph["summary"]["claimCount"] >= 2
    assert graph["summary"]["riskCount"] == 1
    assert graph["summary"]["sourceLocatorCount"] == 1
    assert graph["summary"]["riskRowsWithSourceLocatorCount"] == 1
    assert graph["summary"]["riskRowsWithoutExplicitEvidenceCount"] == 0
    assert any(node["id"] == "evidence:comp-1" for node in graph["nodes"])
    assert any(node["id"] == "risk:R-MKT-001" for node in graph["nodes"])
    assert any(node["id"] == "locator:R-MKT-001:market-sheet:B12" for node in graph["nodes"])
    assert any(edge["from"] == "artifact:marketabilityAnalysis" and edge["to"] == "risk:R-MKT-001" for edge in graph["edges"])
    assert any(edge["from"] == "risk:R-MKT-001" and edge["to"] == "evidence:comp-1" for edge in graph["edges"])
    assert any(edge["from"] == "source:archive-1" and edge["to"] == "risk:R-MKT-001" for edge in graph["edges"])
    risk = run["artifacts"]["riskRegister"]["risks"][0]
    assert risk["evidenceGraphLinks"]["riskNodeId"] == "risk:R-MKT-001"
    assert "claim:marketabilityAnalysis:score" in risk["evidenceGraphLinks"]["claimNodeIds"]
    assert "source:archive-1" in risk["evidenceGraphLinks"]["sourceDocumentNodeIds"]
    assert any(edge["to"] == "report_section:marketAnalysis" for edge in graph["edges"])
    assert graph["unlinkedReportSections"] == []
