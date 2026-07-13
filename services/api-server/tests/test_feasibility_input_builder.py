from use_cases.feasibility_input_builder import FIELDS, build_feasibility_input_draft


def test_builds_from_feasibility_workbook_v2():
    pd = {"feasibilityStudyV2": {"metrics": {"total_revenue": 853951767703, "total_cost": 779350253545}}}
    d = build_feasibility_input_draft(pd)
    assert d["feasibilityInput"]["total_revenue"] == 853951767703
    assert d["method"] == "feasibility_workbook_v2"
    assert d["fieldSources"]["total_revenue"]["source"] == "feasibility_workbook_v2"
    assert d["hasRevenueAndCost"] is True
    assert d["needsReview"] is False  # canonical parsed source → no review flag


def test_existing_input_kept_over_workbook():
    pd = {"feasibility_input": {"total_revenue": 100}, "feasibilityStudyV2": {"metrics": {"total_revenue": 999}}}
    d = build_feasibility_input_draft(pd)
    assert d["feasibilityInput"]["total_revenue"] == 100  # existing input wins


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


def test_no_source_leaves_missing_not_fabricated():
    d = build_feasibility_input_draft({})
    assert d["method"] == "no_source"
    assert set(d["missing"]) == set(FIELDS)
    assert d["feasibilityInput"] == {}
