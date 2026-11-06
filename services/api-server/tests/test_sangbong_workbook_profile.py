"""Independent cell-map checks; synthetic sheets contain no private workbook."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter

from agents.sangbong_workbook_profile import (
    BUSINESS, CASH_FLOW, OVERVIEW, _reader, detect_sangbong_profile,
    map_sangbong_workbook, validate_sangbong_workbook,
)
from schemas.feasibility_study import FeasibilityStudyV2


class Sheet:
    def __init__(self, title, cells):
        self.title, self.cells = title, cells
        bounds = [coordinate_to_tuple(cell) for cell in cells] or [(1, 1)]
        self.max_row = max(row for row, _ in bounds)
        self.max_column = max(col for _, col in bounds)

    def __getitem__(self, cell):
        return SimpleNamespace(value=self.cells.get(cell), coordinate=cell, number_format="General")

    def iter_rows(self):
        for row in range(1, self.max_row + 1):
            yield tuple(self[f"{get_column_letter(col)}{row}"] for col in range(1, self.max_column + 1))


class Book(dict):
    @property
    def sheetnames(self):
        return list(self)

    @property
    def worksheets(self):
        return list(self.values())


def books(variant="sale"):
    overview = {"B5": "대지위치", "B7": "대지면적", "C1": "검증용 복합개발", "C5": "서울시 검증주소",
        "C6": "준주거지역", "D7": 4700, "D11": 4100, "D15": 37000, "C17": 6,
        "C25": 2, "D25": 60, "F25": 80, "H25": 120, "C30": 2, "D30": 120, "H30": 240,
        "C41": 0, "D41": 0, "H41": 0, "C58": 0, "D58": 0, "H58": 0, "D62": 0, "H62": 0}
    business = {"K22": "금 액", "AS3": "단위:평,천원", "AA4": "PF (백만원)"}
    if variant == "sale":
        business.update({"E23": "아파트 분양가", "E69": "토지비", "E123": "PF이자(TR A)",
            "K68": 230, "K146": 200, "K147": 30, "K148": 2, "K149": 28,
            "K46": 210, "K53": 7, "K67": 13,
            "K75": 90, "K77": 70, "K84": 5, "K92": 4, "K104": 8, "K113": 4, "K122": 6, "K145": 13})
    else:
        business.update({"E23": "아파트 임대\n보증금", "E52": "토지비", "E112": "LOAN-TR1 대출이자",
            "U33": 2, "AB33": 100, "U34": 3, "AB34": 120,
            "K25": 50, "K29": 10, "K35": 500, "K32": 20, "K43": 7, "K50": 13, "K51": 600,
            "K134": 200, "K135": 400,
            "K58": 90, "K60": 70, "K67": 5, "K75": 4, "K86": 8, "K94": 2, "K102": 2, "K110": 6, "K133": 13})
    values = {OVERVIEW: overview, BUSINESS: business, CASH_FLOW: {"G108": 15100},
              "분양대금수납계획": {}, "산출기초": {}, "08. 제세금": {}}
    if variant == "rental":
        values["임대조건"] = {}
    formulas = deepcopy(values)
    if variant == "rental":
        formulas[BUSINESS].update({"K35": "=SUM(K33:N34)", "K51": "=SUM(K50,K43,K36)",
                                  "X33": "=AB33-(X26+(K23/U23))", "X34": "=AB34-(X27+(K24/U24))"})
    return (Book({name: Sheet(name, cells) for name, cells in formulas.items()}),
            Book({name: Sheet(name, cells) for name, cells in values.items()}))


@pytest.fixture
def independent_engines(monkeypatch):
    def financing(*args, **kwargs):
        return {"financing": [], "financingTermReview": {"schemaVersion": "sangbong-workbook-financing-review-v1", "terms": []},
                "schedule": [], "pfAmount": {"value": 100000, "rawValue": 0.1, "sourceCell": "'1. 사업수지'!AC7", "sourceUnit": "KRW_MILLION", "status": "read"}}
    monkeypatch.setattr("agents.sangbong_workbook_financing.map_sangbong_financing", financing)
    monkeypatch.setattr("agents.sangbong_workbook_cash_flow.map_sangbong_cash_flow", lambda *args: {
        "monthly_cash_flow": [], "cashFlowPeriodBasis": {}, "cashFlowReview": {"calculationEvidence": {"recalculated": False}}})


@pytest.mark.parametrize("variant", ["sale", "rental"])
def test_structural_detection_requires_semantics_and_declared_units(variant):
    formulas, _ = books(variant)
    assert detect_sangbong_profile(formulas)["variant"] == variant
    formulas[BUSINESS].cells["AS3"] = "단위:원"
    assert detect_sangbong_profile(formulas) is None
    formulas[BUSINESS].cells["AS3"] = "단위:평,천원"
    formulas[BUSINESS].cells["E23"] = "다른 셀 배치"
    assert detect_sangbong_profile(formulas) is None


def test_sale_mapping_preserves_profit_bases_and_does_not_double_count_costs(independent_engines):
    formulas, values = books()
    before = deepcopy(values[BUSINESS].cells)
    draft = map_sangbong_workbook(formulas, values, detect_sangbong_profile(formulas), source_name="synthetic.xlsx")
    assert values[BUSINESS].cells == before
    assert draft["metrics"]["total_revenue"] == 230000
    assert draft["metrics"]["total_cost"] == sum(item["amount"] for item in draft["cost_items"]) == 200000
    assert draft["metrics"]["development_profit"] == 30000
    assert draft["raw_import"]["financialMapping"]["reportedTotals"]["afterVatProfit"]["value"] == 28000
    assert draft["raw_import"]["financialMapping"]["sourceFields"]["other_cost"]["value"] == 27000
    assert draft["site"]["land_area_sqm"]["value"] == 4700
    assert draft["raw_import"]["netSiteArea"]["value"] == 4100
    assert "legal_far" not in draft["site"]
    assert draft["metrics"]["max_minus"] is None and draft["metrics"]["required_equity"] is None
    FeasibilityStudyV2.model_validate(draft)


def test_rental_gross_conversion_excludes_deposit_as_an_extra_revenue(independent_engines):
    formulas, values = books("rental")
    draft = map_sangbong_workbook(formulas, values, detect_sangbong_profile(formulas), source_name="synthetic-rental.xlsx")
    basis = draft["raw_import"]["rentalRevenueBasis"]
    assert basis["status"] == "matched" and basis["grossConversionKrw"] == 560000
    assert sum(item["amount"] for item in draft["revenue_items"]) == 600000
    assert all("deposit" not in item["category"] for item in draft["revenue_items"])
    assert draft["raw_import"]["financialMapping"]["sourceFields"]["total_revenue"]["applyEligible"] is True
    assert draft["raw_import"]["financialMapping"]["reportedTotals"]["initialDeposits"]["value"] == 50000
    FeasibilityStudyV2.model_validate(draft)
    values[BUSINESS].cells["AB34"] = 121
    formulas[BUSINESS].cells["AB34"] = 121
    conflicted = map_sangbong_workbook(formulas, values, detect_sangbong_profile(formulas), source_name="synthetic-rental.xlsx")
    assert conflicted["raw_import"]["rentalRevenueBasis"]["status"] == "needs_review"
    assert conflicted["raw_import"]["financialMapping"]["sourceFields"]["total_revenue"]["applyEligible"] is False


def test_cache_error_blank_and_zero_remain_distinct_and_reader_supports_auxiliary_sheets():
    formulas, values = books()
    formulas[BUSINESS].cells.update(K70="=K69*0.046", K71=0, K72="=#REF!")
    values[BUSINESS].cells.update(K70=1.23456, K71=0, K72="#REF!")
    read = _reader(formulas, values)
    record = read(BUSINESS, "K70", "KRW_THOUSAND")
    assert record["value"] == 1235 and record["rawValue"] == 1.23456
    assert record["formula"] == "=K69*0.046" and record["calculationState"] == "saved_formula_cache"
    assert record["rounding"]["unroundedKrw"] == "1234.56000"
    assert read(BUSINESS, "K71", "KRW_THOUSAND")["value"] == 0
    assert read(BUSINESS, "K72", "KRW_THOUSAND")["value"] is None
    assert read(BUSINESS, "K73", "KRW_THOUSAND")["status"] == "missing"
    assert read(CASH_FLOW, "G108", "KRW_MILLION")["value"] == 15100000000


def test_validation_exposes_unmatched_controls_and_never_claims_recalculation(independent_engines):
    formulas, values = books()
    values[BUSINESS].cells["K146"] = 201
    formulas[BUSINESS].cells["K146"] = 201
    validation = validate_sangbong_workbook(formulas, values, detect_sangbong_profile(formulas),
        formula_integrity={"hasBlockingFormulaErrors": True}, file_name="synthetic.xlsx")
    assert validation["status"] == "needs_review"
    assert validation["templateSupport"]["status"] == "supported_review_mapping"
    assert validation["calculationEvidence"]["recalculated"] is False
    assert validation["schemaReadiness"]["directV2ImportReady"] is False
    assert any(check["passed"] is False for check in validation["reconciliationChecks"])
    assert validation["keyCells"]["total_cost"]["value"] == 201000


def test_public_facilities_and_missing_units_are_not_marketable_zeros(independent_engines):
    formulas, values = books()
    for book in (formulas, values):
        book[OVERVIEW].cells.update(C59=1, D59=50, H59=80, B59="공공업무시설(1F)")
    draft = map_sangbong_workbook(formulas, values, detect_sangbong_profile(formulas), source_name="synthetic.xlsx")
    assert draft["program"]["unit_count"]["value"] == 2
    assert draft["program"]["saleable_area_sqm"]["value"] == 120
    assert all(product["key"] != "source_product_59" for product in draft["product_mix"])
    assert draft["raw_import"]["programBasis"]["excludedPublicFacilities"][0]["units"]["value"] == 1
    for book in (formulas, values):
        for cell in ("C30", "C41", "C58"):
            book[OVERVIEW].cells.pop(cell, None)
    missing = map_sangbong_workbook(formulas, values, detect_sangbong_profile(formulas), source_name="synthetic.xlsx")
    assert missing["program"]["unit_count"]["value"] is None
    assert missing["raw_import"]["programBasis"]["unitCountComplete"] is False


def test_missing_product_detail_cannot_pass_cached_unit_subtotal(independent_engines):
    formulas, values = books()
    for book in (formulas, values):
        book[OVERVIEW].cells.pop("C25", None)
    validation = validate_sangbong_workbook(formulas, values, detect_sangbong_profile(formulas),
        formula_integrity={}, file_name="synthetic.xlsx")
    check = next(item for item in validation["reconciliationChecks"] if item["name"] == "product_units_match_source_total")
    assert check["passed"] is False
    assert check["details"] == {"mappedUnits": 0, "sourceUnits": 2, "matched": False}
