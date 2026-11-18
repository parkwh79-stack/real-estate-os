from use_cases.approved_workbook_authority import GOVERNED_WORKBOOK_CORE_FIELDS
from use_cases.feasibility_input_builder import FIELDS, build_feasibility_input_draft, canonical_product_mix_to_runtime


def test_reapplying_candidate_does_not_remove_review_requirement():
    project = {"feasibility_input": {"total_revenue": 1000, "total_cost": 600}}
    first = build_feasibility_input_draft(project)
    assert first["needsReview"] is True
    project["feasibility_input_sources"] = {
        key: {**source, "value": first["feasibilityInput"].get(key)}
        for key, source in first["fieldSources"].items()
    }
    second = build_feasibility_input_draft(project)
    assert second["needsReview"] is True
    assert second["fieldSources"] == first["fieldSources"]


def _approved_study(metrics):
    snapshot_id = "approved-snapshot-1"
    snapshot_hash = "a" * 64
    return {
        "canonicalFeasibilityStudyV2": {
            "metrics": metrics,
            "input_references": {
                "approved_input_snapshot_id": snapshot_id,
                "approved_input_snapshot_hash": snapshot_hash,
            },
            "raw_import": {
                "canonicalStatus": "approved",
                "parserV2Bridge": {
                    "status": "approved",
                    "approvedInputSnapshotId": snapshot_id,
                    "approvedInputSnapshotHash": snapshot_hash,
                    "approvedFields": list(GOVERNED_WORKBOOK_CORE_FIELDS),
                },
            },
        }
    }


def test_canonical_product_mix_is_materialized_for_runtime_engines():
    rows = canonical_product_mix_to_runtime([{
        "key": "apt-a",
        "asset_type": "apartment",
        "product_name": "아파트 A형",
        "unit_count": 100,
        "contract_area_sqm": 99.1735537,
        "avg_sale_price_per_sqm": 9_917_355.37,
        "revenue": 300_000_000_000,
    }])
    assert rows[0]["type"] == "아파트 A형"
    assert rows[0]["units"] == 100
    assert round(rows[0]["contractAreaPy"], 2) == 30.0
    assert round(rows[0]["pricePerPy"]) == 32_784_646
    assert rows[0]["totalRevenue"] == 300_000_000_000
    assert rows[0]["source"] == "canonical_feasibility_study_v2"


def test_builds_from_feasibility_workbook_v2():
    pd = _approved_study({"total_revenue": 853951767703, "total_cost": 779350253545})
    d = build_feasibility_input_draft(pd)
    assert d["feasibilityInput"]["total_revenue"] == 853951767703
    assert d["method"] == "feasibility_workbook_v2"
    assert d["fieldSources"]["total_revenue"]["source"] == "feasibility_workbook_v2"
    assert d["hasRevenueAndCost"] is True
    assert d["needsReview"] is False  # canonical parsed source → no review flag


def test_approved_workbook_wins_over_stale_feasibility_input():
    # The approved 사업수지 workbook (feasibilityStudyV2) is authoritative — it must win
    # over a stale/placeholder feasibility_input (e.g. auto_supplement 750/600억).
    pd = {
        **_approved_study({"total_revenue": 853_951_767_703}),
        "feasibility_input": {"total_revenue": 75_000_000_000},
    }
    d = build_feasibility_input_draft(pd)
    assert d["feasibilityInput"]["total_revenue"] == 853_951_767_703
    assert d["fieldSources"]["total_revenue"]["source"] == "feasibility_workbook_v2"


def test_existing_input_fills_field_workbook_lacks():
    pd = {
        **_approved_study({"total_revenue": 100_000_000_000, "total_cost": 80_000_000_000}),
        "feasibility_input": {"finance_cost": 5_000_000_000},
    }
    d = build_feasibility_input_draft(pd)
    assert d["feasibilityInput"]["finance_cost"] == 5_000_000_000  # workbook lacks it → existing fills
    assert d["fieldSources"]["finance_cost"]["source"] == "feasibility_input"


def test_unapproved_study_never_becomes_authoritative():
    pd = {
        "feasibilityStudyV2": {"metrics": {"total_revenue": 900_000_000_000}},
        "feasibilityStudyV2Draft": {"metrics": {"total_revenue": 800_000_000_000}},
        "feasibility_input": {"total_revenue": 70_000_000_000},
    }
    d = build_feasibility_input_draft(pd)
    assert d["feasibilityInput"]["total_revenue"] == 70_000_000_000
    assert d["fieldSources"]["total_revenue"]["source"] == "feasibility_input"


def test_canonical_status_without_snapshot_binding_is_not_authoritative():
    pd = {
        "canonicalFeasibilityStudyV2": {
            "metrics": {"total_revenue": 900_000_000_000},
            "raw_import": {"canonicalStatus": "approved"},
        },
        "feasibility_input": {"total_revenue": 70_000_000_000},
    }

    d = build_feasibility_input_draft(pd)

    assert d["feasibilityInput"]["total_revenue"] == 70_000_000_000
    assert d["fieldSources"]["total_revenue"]["source"] == "feasibility_input"


def test_derives_land_and_construction_from_program_flagged_review():
    pd = {"land": {"area_sqm": 5000, "officialLandPricePerSqm": 1_000_000}, "regulation": {"applied_far": 300}}
    d = build_feasibility_input_draft(pd)
    assert d["feasibilityInput"]["land_cost"] == 5_000_000_000  # 공시지가 × 면적
    # GFA = 토지 5000 × FAR 300/100 = 15000㎡ × 공사비 벤치마크 3,000,000
    assert d["feasibilityInput"]["construction_cost"] == 15000 * 3_000_000
    assert d["fieldSources"]["land_cost"]["needsReview"] is True
    # total_cost = land + construction
    assert d["feasibilityInput"]["total_cost"] == d["feasibilityInput"]["land_cost"] + d["feasibilityInput"]["construction_cost"]
    assert d["needsReview"] is True


def test_parsed_fact_feasibility_field():
    facts = [{"canonicalField": "feasibility_input.total_revenue", "normalizedValue": 5000}]
    d = build_feasibility_input_draft({}, parsed_facts=facts)
    assert d["feasibilityInput"]["total_revenue"] == 5000
    assert d["fieldSources"]["total_revenue"]["source"] == "parsed_fact"


def test_zero_parsed_fact_ignored():
    # auto_supplement placeholder zeros must not populate feasibility_input
    facts = [{"canonicalField": "feasibility_input.total_revenue", "normalizedValue": 0.0}]
    d = build_feasibility_input_draft({}, parsed_facts=facts)
    assert "total_revenue" not in d["feasibilityInput"]


def test_auto_supplement_parsed_fact_flagged_as_proxy():
    # auto_supplement placeholders are proxies, not real 사업수지 — must be labelled low-confidence.
    facts = [
        {"canonicalField": "feasibility_input.total_revenue", "normalizedValue": 75_000_000_000, "factOrigin": "auto_supplement"},
        {"canonicalField": "feasibility_input.total_cost", "normalizedValue": 60_000_000_000, "factOrigin": "auto_supplement"},
    ]
    d = build_feasibility_input_draft({}, parsed_facts=facts)
    assert d["fieldSources"]["total_revenue"]["source"] == "auto_supplement_proxy"
    assert d["fieldSources"]["total_revenue"]["confidence"] == 0.25
    assert d["needsReview"] is True


def test_real_parsed_fact_not_marked_proxy():
    facts = [{"canonicalField": "feasibility_input.total_revenue", "normalizedValue": 5000, "factOrigin": "document_parse"}]
    d = build_feasibility_input_draft({}, parsed_facts=facts)
    assert d["fieldSources"]["total_revenue"]["source"] == "parsed_fact"


def test_no_source_leaves_missing_not_fabricated():
    d = build_feasibility_input_draft({})
    assert d["method"] == "no_source"
    assert set(d["missing"]) == set(FIELDS)
    assert d["feasibilityInput"] == {}
