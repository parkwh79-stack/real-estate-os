from use_cases.feasibility_from_program import build_feasibility_from_program


def _project():
    return {
        "sales_assumptions": {"product_mix": [
            {"type": "전용129㎡ (일반분양)", "units": 369, "contractAreaPy": 48, "pricePerPy": 0},
            {"type": "전용84㎡ (공공지원민간임대)", "units": 390, "contractAreaPy": 34, "pricePerPy": 0},
        ]},
        "architectural_plan": {"gross_floor_area_sqm": 211969},
        "land": {"area_sqm": 66971, "officialLandPricePerSqm": 1_466_000},
        "market": {"proposedPricePerPy": 22_831_415},
    }


def test_revenue_from_product_mix_with_comparable_and_rental_factor():
    r = build_feasibility_from_program(_project())
    items = {i["type"]: i for i in r["productRevenue"] if i.get("revenue")}
    # 일반분양 uses the comparable directly; 임대 uses comparable × 0.7 (전환 가정)
    assert items["전용129㎡ (일반분양)"]["pricePerPy"] == 22_831_415
    assert items["전용84㎡ (공공지원민간임대)"]["pricePerPy"] == round(22_831_415 * 0.7)
    expected_revenue = 22_831_415 * 48 * 369 + round(22_831_415 * 0.7) * 34 * 390
    assert r["feasibilityInput"]["total_revenue"] == expected_revenue
    assert r["feasibilityInput"]["land_cost"] == 1_466_000 * 66971
    assert r["feasibilityInput"]["construction_cost"] == 211969 * 3_000_000
    assert r["hasRevenueAndCost"] is True
    assert r["needsReview"] is True and r["assumptions"]


def test_no_price_basis_leaves_revenue_unset():
    pd = {"sales_assumptions": {"product_mix": [{"type": "일반분양", "units": 100, "contractAreaPy": 30, "pricePerPy": 0}]}}
    r = build_feasibility_from_program(pd)
    assert "total_revenue" not in r["feasibilityInput"]  # no comparable/override → not fabricated


def test_override_sale_price():
    pd = {"sales_assumptions": {"product_mix": [{"type": "일반분양", "units": 100, "contractAreaPy": 30, "pricePerPy": 0}]}}
    r = build_feasibility_from_program(pd, options={"sale_price_per_py": 20_000_000})
    assert r["feasibilityInput"]["total_revenue"] == 20_000_000 * 30 * 100


def test_parsed_price_preferred_over_estimate():
    pd = {"sales_assumptions": {"product_mix": [{"type": "일반분양", "units": 10, "contractAreaPy": 30, "pricePerPy": 15_000_000}]}, "market": {"proposedPricePerPy": 99_000_000}}
    r = build_feasibility_from_program(pd)
    item = next(i for i in r["productRevenue"] if i.get("revenue"))
    assert item["pricePerPy"] == 15_000_000  # parsed price wins over comparable
    assert item["priceSource"] == "parsed"
