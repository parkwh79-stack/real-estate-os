from agents.parser_provenance import attach_parser_field_provenance


def test_attach_parser_field_provenance_covers_non_market_fields_without_fabrication():
    parsed_data = {
        "project": {"name": "Bonri Mixed Use PF"},
        "land": {"address": "Daegu Bonri-dong", "area_sqm": 4691.9},
        "regulation": {"applied_far": 857.4},
        "feasibility_input": {"total_revenue": 165_000_000_000},
        "pf_structure": [{"tranche": "Tr.A", "amount": 63_000_000_000}],
        "schedule": {"completion": "2023-10"},
        "covenants": {"drawdown_conditions": ["permit approval"]},
        "market": {"comparables": [{"pricePerPy": 30_000_000}]},
        "source_locators": {
            "project.name": {"pageNumber": 1, "fieldName": "project.name", "textExcerpt": "Bonri Mixed Use PF"},
            "land.area_sqm": {"pageNumber": 5, "table": "site summary", "fieldName": "land.area_sqm"},
            "pf_structure[0].amount": {"pageNumber": 2, "table": "loan terms", "row": "Tr.A"},
            "schedule.completion": {"pageNumber": 8, "fieldName": "completion"},
            "covenants.drawdown_conditions": {"pageNumber": 12, "fieldName": "drawdown conditions"},
        },
    }

    result = attach_parser_field_provenance(
        parsed_data,
        upload_meta={"originalFileName": "business-plan.pdf"},
        parser_version="test-parser",
    )

    provenance = result["_meta"]["parserFieldProvenance"]
    assert provenance["schemaVersion"] == "realytics-parser-field-provenance-v1"
    assert provenance["coverage"]["fieldCount"] >= 7
    assert provenance["coverage"]["locatedFieldCount"] == 5
    assert provenance["coverage"]["status"] == "partial"
    assert provenance["coverage"]["domains"]["land"]["fieldCount"] == 2
    assert "market.comparables[0].pricePerPy" not in result["parser_field_source_locators"]
    assert result["parser_field_source_locators"]["pf_structure[0].amount"]["sourceType"] == "document_page"
    assert any(field["path"] == "regulation.applied_far" for field in provenance["missingFields"])
    assert result["parser_term_surfaces"]

    revenue_field = next(field for field in provenance["fields"] if field["path"] == "feasibility_input.total_revenue")
    assert revenue_field["termId"] == "total_sales_revenue"
    assert revenue_field["canonicalField"] == "feasibility_input.total_revenue"
    assert revenue_field["termSurface"]["rawText"] == "total_sales_revenue"
    assert revenue_field["termSurface"]["normalizedSurface"] == "total_sales_revenue"
    assert "textQualityFlags" in revenue_field["termSurface"]

    senior_amount = next(field for field in provenance["fields"] if field["path"] == "pf_structure[0].amount")
    assert senior_amount["termId"] == "senior_tranche"
    assert senior_amount["canonicalField"] == "pf_structure.tranches.senior.amount"
    assert senior_amount["termSurface"]["sourceLocator"]["sourceType"] == "document_page"

    missing_regulation = next(field for field in provenance["missingFields"] if field["path"] == "regulation.applied_far")
    assert missing_regulation["termSurface"]["canonicalField"] == "regulation.applied_far"
    assert missing_regulation["termSurface"]["sourceLocator"]["sourceType"] == "parsed_field_path"
