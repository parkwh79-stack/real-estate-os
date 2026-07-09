from use_cases.chief_deal_orchestration import plan_chief_deal_orchestration_use_case
from use_cases.auto_evidence_supplement import auto_supplement_validation_gaps
from use_cases.parsed_facts import persist_parsed_facts, parsed_fact_package
from use_cases.parsed_fact_validation import validate_parsed_facts_for_pod
from storage import list_deal_state_records


def _setup_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "parsed-facts.sqlite3"))


def test_persist_parsed_facts_materializes_parser_and_market_values(monkeypatch, tmp_path):
    _setup_storage(monkeypatch, tmp_path)
    parsed_data = {
        "_meta": {
            "upload": {
                "archiveId": "doc-1",
                "originalFileName": "business-plan.pdf",
                "documentType": "business_plan",
                "uploadedAt": "2026-07-07T00:00:00Z",
            },
            "parser_version": "parser-test-v1",
            "parserFieldProvenance": {
                "fields": [
                    {
                        "path": "feasibility_input.total_revenue",
                        "domain": "feasibility_input",
                        "valuePreview": 120000000000,
                        "sourceLocator": {"documentName": "business-plan.pdf", "pageNumber": 12, "table": "sources-and-uses"},
                        "locatorStatus": "located",
                        "locatorPrecision": "page",
                        "termId": "total_sales_revenue",
                        "canonicalField": "feasibility_input.total_revenue",
                        "termSurface": {
                            "rawText": "total_sales_revenue",
                            "normalizedSurface": "total_sales_revenue",
                            "textQualityFlags": {},
                            "termId": "total_sales_revenue",
                            "canonicalField": "feasibility_input.total_revenue",
                            "confidence": 0.8,
                        },
                    },
                    {
                        "path": "land.area_sqm",
                        "domain": "land",
                        "valuePreview": 5000,
                        "sourceLocator": {},
                        "locatorStatus": "missing",
                        "locatorPrecision": "missing",
                        "termId": "land_area_sqm",
                        "canonicalField": "land.area_sqm",
                    },
                ],
            },
        },
        "feasibility_input": {"total_revenue": 120000000000},
        "land": {"area_sqm": 5000},
        "document_market_evidence": {
            "sourceDocument": {"archiveId": "doc-1", "originalFileName": "business-plan.pdf"},
            "proposedPricePerPy": 40000000,
            "productMix": [
                {
                    "type": "apartment",
                    "units": 100,
                    "pricePerPy": 40000000,
                    "totalRevenue": 120000000000,
                    "sourceLocator": {"documentName": "business-plan.pdf", "pageNumber": 15, "table": "product-mix", "row": "1"},
                }
            ],
            "manualComparables": [
                {
                    "name": "Comparable A",
                    "pricePerPy": 35000000,
                    "sourceLocator": {"documentName": "business-plan.pdf", "pageNumber": 22, "table": "comps", "row": "2"},
                }
            ],
        },
    }

    result = persist_parsed_facts("p_parsed", parsed_data)
    records = list_deal_state_records("p_parsed", record_type="parsed_fact", limit=100)
    package = parsed_fact_package("p_parsed")

    assert result["recordCount"] >= 7
    assert package["recordCount"] == len(records)
    by_key = {record["canonicalField"]: record for record in records if record.get("canonicalField")}
    assert by_key["feasibility_input.total_revenue"]["normalizedValue"] == 120000000000
    assert by_key["feasibility_input.total_revenue"]["targetPods"] == ["feasibility_cashflow"]
    assert by_key["land.area_sqm"]["validationStatus"] == "needs_source_locator"
    assert by_key["market.proposedPricePerPy"]["targetPods"] == ["market_product"]
    assert "rawText" in by_key["market.proposedPricePerPy"]
    assert "textQualityFlags" in by_key["market.proposedPricePerPy"]
    assert package["summary"]["criticalMissingLocatorCount"] >= 1


def test_chief_reconciliation_flags_parsed_fact_value_conflicts():
    project_data = {
        "project": {"id": "p_conflict", "name": "Parsed fact conflict"},
        "source_documents": [{"documentType": "business_plan", "originalFileName": "business.pdf", "archiveId": "bp1"}],
        "parsedFacts": [
            {
                "recordId": "parsed_fact:a",
                "canonicalField": "feasibility_input.total_revenue",
                "value": 100000000000,
                "normalizedValue": 100000000000,
                "sourceLocator": {"documentName": "business.pdf", "pageNumber": 10},
                "locatorStatus": "located",
                "targetPods": ["feasibility_cashflow", "pf_credit"],
                "critical": True,
            },
            {
                "recordId": "parsed_fact:b",
                "canonicalField": "feasibility_input.total_revenue",
                "value": 104000000000,
                "normalizedValue": 104000000000,
                "sourceLocator": {"sheet": "사업수지", "cell": "B12"},
                "locatorStatus": "located",
                "targetPods": ["feasibility_cashflow", "pf_credit"],
                "critical": True,
            },
        ],
        "land": {"pnu": "1111010100100010000", "officialLandPricePerSqm": 1000000},
        "landEvidence": [{"evidenceType": "official_land_price", "pnu": "1111010100100010000"}],
        "market": {"manualComparables": [{"name": "A", "pricePerPy": 35000000}]},
        "product_mix": [{"type": "apartment", "units": 10}],
        "feasibility_input": {"total_revenue": 100000000000, "total_cost": 80000000000},
        "pf_structure": [{"tranche": "Senior", "amount": 50000000000}],
    }

    plan = plan_chief_deal_orchestration_use_case("p_conflict", project_data, {"workflowTrack": "pf"})
    conflicts = {row["code"]: row for row in plan["sharedDealStateGraph"]["conflicts"]}

    assert "PARSED_FACT_VALUE_CONFLICT:feasibility_input.total_revenue" in conflicts
    conflict = conflicts["PARSED_FACT_VALUE_CONFLICT:feasibility_input.total_revenue"]
    assert conflict["severity"] == "high"
    assert conflict["values"]["canonicalField"] == "feasibility_input.total_revenue"


def test_land_legal_pod_validation_materializes_required_evidence_risks(monkeypatch, tmp_path):
    _setup_storage(monkeypatch, tmp_path)
    persist_parsed_facts(
        "p_land",
        {
            "_meta": {
                "upload": {"archiveId": "land-doc", "originalFileName": "land-ledger.xlsx"},
                "parserFieldProvenance": {
                    "fields": [
                        {
                            "path": "land.area_sqm",
                            "domain": "land",
                            "valuePreview": 11234.5,
                            "sourceLocator": {},
                            "locatorStatus": "missing",
                            "termId": "land_area_sqm",
                            "canonicalField": "land.area_sqm",
                        }
                    ]
                },
            },
            "land": {"area_sqm": 11234.5},
        },
    )

    result = validate_parsed_facts_for_pod("p_land", "land_legal", source_artifact_key="artifact:land_legal:1")

    assert result["summary"]["candidateFactCount"] == 1
    codes = {finding["riskCode"] for finding in result["findings"]}
    assert "PARSED_FACT_SOURCE_LOCATOR_WEAK" in codes
    assert "PARSED_FACT_REQUIRED_MISSING" in codes
    facts = list_deal_state_records("p_land", record_type="parsed_fact", limit=50)
    assert facts[0]["podValidationStatusByPod"]["land_legal"]["status"] == "needs_source_locator"
    risks = list_deal_state_records("p_land", record_type="risk_issue", limit=50)
    assert any(risk["riskCode"] == "PARSED_FACT_REQUIRED_MISSING" for risk in risks)


def test_market_product_validation_separates_price_basis_from_comparable_data(monkeypatch, tmp_path):
    _setup_storage(monkeypatch, tmp_path)
    persist_parsed_facts(
        "p_market",
        {
            "_meta": {"upload": {"archiveId": "mkt-doc", "originalFileName": "market.pdf"}},
            "document_market_evidence": {
                "sourceDocument": {"archiveId": "mkt-doc", "originalFileName": "market.pdf"},
                "manualComparables": [
                    {
                        "name": "Comparable A",
                        "pricePerPy": 35000000,
                        "sourceLocator": {"documentName": "market.pdf", "pageNumber": 4, "table": "comps", "row": "A"},
                    }
                ],
            },
        },
    )

    result = validate_parsed_facts_for_pod("p_market", "market_product", source_artifact_key="artifact:market_product:1")

    codes = {finding["riskCode"] for finding in result["findings"]}
    assert "PROPOSED_PRICE_BASIS_MISSING" in codes
    assert not any(
        finding["riskCode"] == "PARSED_FACT_REQUIRED_MISSING"
        and (finding.get("details") or {}).get("canonicalField") == "marketComparable.pricePerPy"
        for finding in result["findings"]
    )
    risks = list_deal_state_records("p_market", record_type="risk_issue", limit=50)
    assert any(risk["riskCode"] == "PROPOSED_PRICE_BASIS_MISSING" for risk in risks)


def test_auto_supplement_uses_comparables_as_reviewable_price_basis(monkeypatch, tmp_path):
    _setup_storage(monkeypatch, tmp_path)
    project_data = {
        "project": {"type": "apartment"},
        "market": {
            "manualComparables": [
                {"name": "A", "pricePerPy": 33000000, "sourceType": "manual_upload", "sourceUrlOrEvidenceId": "manual:A"},
                {"name": "B", "pricePerPy": 35000000, "sourceType": "manual_upload", "sourceUrlOrEvidenceId": "manual:B"},
                {"name": "C", "pricePerPy": 37000000, "sourceType": "manual_upload", "sourceUrlOrEvidenceId": "manual:C"},
            ]
        },
    }
    persist_parsed_facts(
        "p_auto_market",
        {
            "_meta": {"upload": {"archiveId": "mkt-doc", "originalFileName": "market.pdf"}},
            "document_market_evidence": {
                "sourceDocument": {"archiveId": "mkt-doc", "originalFileName": "market.pdf"},
                "manualComparables": project_data["market"]["manualComparables"],
            },
        },
    )
    initial = validate_parsed_facts_for_pod("p_auto_market", "market_product")
    assert any(f["riskCode"] == "PROPOSED_PRICE_BASIS_MISSING" for f in initial["findings"])

    supplement = auto_supplement_validation_gaps("p_auto_market", "market_product", initial, project_data)
    final = validate_parsed_facts_for_pod("p_auto_market", "market_product")

    assert supplement["summary"]["createdRecordCount"] >= 2
    assert not any(f["riskCode"] == "PROPOSED_PRICE_BASIS_MISSING" for f in final["findings"])
    facts = list_deal_state_records("p_auto_market", record_type="parsed_fact", limit=100)
    supplemented = [fact for fact in facts if fact.get("factOrigin") == "auto_supplement"]
    assert any(fact["canonicalField"] == "market.proposedPricePerPy" for fact in supplemented)
    assumptions = list_deal_state_records("p_auto_market", record_type="assumption", limit=50)
    assert any(item["assumptionType"] == "benchmark_proxy_not_project_proposed_price" for item in assumptions)
    risks = list_deal_state_records("p_auto_market", record_type="risk_issue", limit=100)
    assert any(risk["riskCode"] == "PROPOSED_PRICE_BASIS_SUPPLEMENTED_NEEDS_REVIEW" for risk in risks)


def test_auto_supplement_promotes_project_land_memory_to_evidence(monkeypatch, tmp_path):
    _setup_storage(monkeypatch, tmp_path)
    project_data = {
        "land": {
            "pnu": "2729011400100010003",
            "officialLandPricePerSqm": 3216000,
            "officialLandPriceBaseYear": "2025",
            "officialLandPriceSource": "VWorld cadastral jiga",
        }
    }
    initial = validate_parsed_facts_for_pod("p_auto_land", "land_legal")
    assert any(f["riskCode"] == "PARSED_FACT_REQUIRED_MISSING" for f in initial["findings"])

    supplement = auto_supplement_validation_gaps("p_auto_land", "land_legal", initial, project_data)
    final = validate_parsed_facts_for_pod("p_auto_land", "land_legal")

    assert supplement["summary"]["createdRecordCount"] >= 2
    assert not any(
        f["riskCode"] == "PARSED_FACT_REQUIRED_MISSING"
        and (f.get("details") or {}).get("canonicalField") == "land.pnu"
        for f in final["findings"]
    )
    facts = list_deal_state_records("p_auto_land", record_type="parsed_fact", limit=100)
    assert any(fact.get("canonicalField") == "land.pnu" for fact in facts)
    assert any(fact.get("canonicalField") == "landEvidence.officialLandPrice.pricePerSqm" for fact in facts)
    evidence = list_deal_state_records("p_auto_land", record_type="evidence", limit=100)
    assert any(item.get("evidenceType") == "official_land_price" for item in evidence)
