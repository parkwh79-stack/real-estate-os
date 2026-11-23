"""Build a feasibility_input from the parsed development program (AI-assisted).

For projects where the parser extracted the program (연면적, 세대수, 상품구성) but no
prices, this composes a feasibility (사업수지) draft from:
  - 상품구성(product_mix): 세대수 × 계약면적(평) × 평당가
      · 일반분양 → 평당가 = 시장 비교사례(실거래) 가격 (or caller override)
      · 공공지원민간임대/공공임대 → 평당가 = 분양가 × 임대전환 factor (가정, override 가능)
  - 토지비 = 공시지가 × 토지면적 (추정 — 실제 매입가 확인 필요; or override)
  - 공사비 = 연면적 × 공사비 단가 벤치마크 (or override)

Invariants: prices/costs that come from a market comparable, benchmark, or an
assumption factor are labelled and returned in ``assumptions`` with ``needsReview``.
Where there is no basis at all (no comparable, no override) revenue stays unset —
never invented. Every numeric input is arithmetic, not an LLM output.
"""

from __future__ import annotations

from math import isfinite
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from domains.feasibility.numeric import excel_round_int, to_decimal
from domains.feasibility.regulatory import (
    build_preliminary_buildable_envelope, evaluate_program_eligibility,
    included_parcel_area, is_excluded_parcel, regulatory_parcels,
)
from use_cases.parcel_pnu_resolution import parcel_pnu

FEASIBILITY_FROM_PROGRAM_SCHEMA = "realytics-feasibility-from-program-v1"

DEFAULT_CONSTRUCTION_COST_PER_SQM = 3_000_000  # 연면적당 공사비 벤치마크(원/㎡)
DEFAULT_RENTAL_SALE_FACTOR = 0.7  # 공공지원민간임대 매각/분양전환가 = 분양가 × factor (가정)
_RENTAL_MARKERS = ("임대", "공공")


class DrawingAreaSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    grossFloorAreaSqm: Annotated[float, Field(gt=0, le=1_000_000_000)]
    archiveId: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    revision: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    location: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        n = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return n if isfinite(n) and n > 0 else None


def _parsed(parsed_facts: Optional[List[Dict[str, Any]]], canonical: str) -> Optional[float]:
    for record in parsed_facts or []:
        if str(record.get("canonicalField") or "") == canonical:
            v = _num(record.get("normalizedValue"))
            if v is None:
                v = _num(record.get("value"))
            if v:
                return v
    return None


def _land_area(project_data: Dict[str, Any], parsed_facts) -> Optional[float]:
    return _num((project_data.get("land") or {}).get("area_sqm")) or _parsed(parsed_facts, "land.area_sqm")


def _official_land_price(project_data: Dict[str, Any], parsed_facts) -> Optional[float]:
    direct = _num((project_data.get("land") or {}).get("officialLandPricePerSqm"))
    if direct:
        return direct
    for record in parsed_facts or []:
        cf = str(record.get("canonicalField") or "")
        if "officialLandPrice" in cf and "perSqm" in cf.replace("pricePerSqm", "perSqm"):
            v = _num(record.get("normalizedValue")) or _num(record.get("value"))
            if v:
                return v
    return None


def _comparable_price_per_py(project_data: Dict[str, Any], parsed_facts) -> Optional[float]:
    market = project_data.get("market") or {}
    depth = project_data.get("marketProductDepth") or {}
    for cand in (market.get("proposedPricePerPy"), depth.get("publicTransactionMedianPerPy")):
        v = _num(cand)
        if v:
            return v
    return _parsed(parsed_facts, "marketComparable.pricePerPy")


def _parcel_land_cost(project_data: Dict[str, Any], envelope: dict) -> dict:
    rows = []
    seen = set()
    duplicate = False
    for index, parcel in enumerate(regulatory_parcels(project_data), start=1):
        pnu = parcel_pnu(parcel)
        excluded = is_excluded_parcel(parcel)
        area = included_parcel_area(parcel) if not excluded else None
        price = _num(parcel.get("officialLandPricePerSqm")) if not excluded else None
        locators = parcel.get("parcelSourceLocators") or []
        source = next((locator for locator in locators if isinstance(locator, dict)), {})
        row = {
            "parcelIndex": index, "pnu": pnu or None, "excluded": excluded,
            "ledgerAreaSqm": _num(parcel.get("areaSqm") or parcel.get("area_sqm")),
            "includedAreaSqm": float(area) if area is not None else None,
            "unitPriceKrw": price, "estimatedCostKrw": excel_round_int(area * to_decimal(price)) if area and price else None,
            "sourceLocator": {key: source[key] for key in ("archiveId", "sourceDocumentId", "sheetName", "cell", "rowNumber")
                              if isinstance(source.get(key), (str, int))},
        }
        if not excluded and pnu:
            duplicate = duplicate or pnu in seen
            seen.add(pnu)
        rows.append(row)
    included = [row for row in rows if not row["excluded"]]
    amounts = [row["estimatedCostKrw"] for row in included if row["estimatedCostKrw"] is not None]
    area_mismatch = "SITE_AREA_MISMATCH" in envelope["blockingCodes"]
    complete = (bool(included) and all(row["pnu"] for row in included)
                and len(amounts) == len(included) and not (duplicate or area_mismatch))
    return {
        "kind": "parcel_estimate", "status": "estimated" if complete else "unconfirmed",
        "amountKrw": sum(amounts) if complete else None,
        "includedAreaSqm": envelope["siteParcelSnapshot"]["totalIncludedAreaSqm"],
        "parcelSetHash": envelope["siteParcelSnapshot"]["parcelSetHash"],
        "unitPriceKrw": None,
        "parcels": rows,
    }


def _product_mix(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    sa = project_data.get("sales_assumptions") or {}
    for source in (sa.get("product_mix"), project_data.get("product_mix"), project_data.get("productMix")):
        if isinstance(source, list) and source:
            return [item for item in source if isinstance(item, dict)]
    return []


def build_feasibility_from_program(
    project_data: Dict[str, Any],
    parsed_facts: Optional[List[Dict[str, Any]]] = None,
    options: Optional[Dict[str, Any]] = None,
    *,
    drawing: dict | None = None,
) -> Dict[str, Any]:
    options = options or {}
    assumptions: List[str] = []

    land_area = _num(options.get("land_area_sqm")) or _land_area(project_data, parsed_facts)
    parcel_envelope = build_preliminary_buildable_envelope(project_data, declared_land_area_sqm=land_area)
    parcel_far = (parcel_envelope or {}).get("effectiveLegalFar")
    plan = project_data.get("architectural_plan") or {}
    gfa = drawing["grossFloorAreaSqm"] if drawing else _num(plan.get("gross_floor_area_sqm"))
    applied_far = _num(options.get("applied_far")) or _num((project_data.get("regulation") or {}).get("applied_far"))
    if land_area and not applied_far and parcel_far:
        applied_far = parcel_far
        assumptions.append("필지별 용적률을 합산한 결과는 규모 검토 후보입니다.")
    if parcel_envelope and parcel_envelope["blockingCodes"]:
        assumptions.append("필지 규제 용량 산정 전 누락·충돌 자료를 확인하세요.")
    comparable = _num(options.get("sale_price_per_py")) or _comparable_price_per_py(project_data, parsed_facts)
    rental_factor = _num(options.get("rental_sale_factor")) or DEFAULT_RENTAL_SALE_FACTOR

    # --- Revenue from product mix ---
    product_mix = _product_mix(project_data)
    items: List[Dict[str, Any]] = []
    total_revenue = 0
    for pm in product_mix:
        units = _num(pm.get("units"))
        contract = _num(pm.get("contractAreaPy") or pm.get("contract_area_py"))
        label = str(pm.get("type") or pm.get("productType") or "")
        is_rental = any(marker in label for marker in _RENTAL_MARKERS)
        price = _num(pm.get("pricePerPy") or pm.get("price_per_py"))
        price_source = "parsed" if price else None
        if not price and comparable:
            price = excel_round_int(to_decimal(comparable) * to_decimal(rental_factor if is_rental else 1.0))
            price_source = "rental_conversion_estimate" if is_rental else "market_comparable_estimate"
        if not (units and contract and price):
            items.append({"type": label, "units": int(units) if units else None, "status": "no_price"})
            continue
        revenue = excel_round_int(to_decimal(price) * to_decimal(contract) * to_decimal(units))
        total_revenue += revenue
        items.append({
            "type": label,
            "units": int(units),
            "contractAreaPy": contract,
            "pricePerPy": excel_round_int(price),
            "isRental": is_rental,
            "priceSource": price_source,
            "revenue": revenue,
        })
    if comparable and any(i.get("priceSource", "").endswith("estimate") for i in items):
        assumptions.append(
            f"평당가 미기재 → 일반분양 = 시장 비교사례 {round(comparable/10000):,}만원/평, "
            f"임대(공공지원민간임대·공공임대) = 그 {rental_factor:g}배로 추정(매각/전환가 가정) — 실제 분양가·임대료 확인 필요."
        )

    # --- Costs ---
    land_cost = _num(options.get("land_cost"))
    land_basis = None
    if land_cost is None and parcel_envelope:
        land_basis = _parcel_land_cost(project_data, parcel_envelope)
        land_cost = land_basis["amountKrw"]
        assumptions.append("편입 대상으로 기재된 필지의 면적·공시지가를 사용한 토지비 추정입니다."
                           if land_cost is not None else "편입 필지의 면적·공시지가·중복 여부와 대지면적 합계를 확인하세요.")
    elif land_cost is None and land_area:
        land_price = _official_land_price(project_data, parsed_facts)
        if land_price:
            land_cost = excel_round_int(to_decimal(land_price) * to_decimal(land_area))
            land_basis = {"kind": "site_estimate", "status": "estimated", "includedAreaSqm": land_area,
                          "unitPriceKrw": land_price, "amountKrw": land_cost, "parcelSetHash": None, "parcels": []}
            assumptions.append("기재 대지면적과 공시지가를 곱한 토지비 추정입니다.")
    construction_cost = _num(options.get("construction_cost"))
    construction_basis = None
    if construction_cost is None:
        ccps = _num(options.get("construction_cost_per_sqm")) or DEFAULT_CONSTRUCTION_COST_PER_SQM
        construction_cost = excel_round_int(to_decimal(gfa) * to_decimal(ccps)) if gfa else None
        construction_basis = {
            "kind": "drawing" if drawing else "declared_area", "status": "estimated" if gfa else "unconfirmed",
            "grossFloorAreaSqm": gfa, "unitPriceKrw": ccps, "amountKrw": construction_cost,
            "revision": None, "sourceLocator": None,
        }
        if drawing:
            construction_basis.update(revision=drawing["revision"], sourceLocator=drawing["sourceLocator"])
        assumptions.append("시공 연면적과 ㎡당 단가를 곱한 공사비 추정입니다."
                           if gfa else "시공 연면적 미확인으로 면적 기반 공사비를 산정하지 않았습니다.")
    finance_cost = _num(options.get("finance_cost")) or 0.0
    total_cost = land_cost + construction_cost + finance_cost if land_cost and construction_cost else None

    feasibility_input: Dict[str, Any] = {}
    if total_revenue > 0:
        feasibility_input["total_revenue"] = excel_round_int(total_revenue)
    if land_cost:
        feasibility_input["land_cost"] = excel_round_int(land_cost)
    if construction_cost:
        feasibility_input["construction_cost"] = excel_round_int(construction_cost)
    if finance_cost:
        feasibility_input["finance_cost"] = excel_round_int(finance_cost)
    if total_cost:
        feasibility_input["total_cost"] = excel_round_int(total_cost)

    profit = None
    margin = None
    if feasibility_input.get("total_revenue") and feasibility_input.get("total_cost"):
        profit = feasibility_input["total_revenue"] - feasibility_input["total_cost"]
        margin = round(profit / feasibility_input["total_revenue"], 4)

    regulation = project_data.get("regulation") or {}
    legal_far = parcel_far if parcel_envelope else _num(regulation.get("legal_far") or regulation.get("legalFar"))
    constraint_snapshot_id = parcel_envelope.get("regulatoryConstraintSnapshotId") if parcel_envelope else None
    scenario_eligibility = evaluate_program_eligibility(
        applied_far=applied_far,
        legal_far=legal_far,
        constraint_snapshot_id=constraint_snapshot_id,
        constraint_approved=bool(parcel_envelope and parcel_envelope.get("reportGrade")),
    )
    if parcel_envelope and parcel_envelope.get("blockingCodes"):
        scenario_eligibility["eligibility"] = "not_assessable"
        scenario_eligibility["reportGrade"] = False
        scenario_eligibility["blockingConstraintIds"] = sorted(set(
            scenario_eligibility.get("blockingConstraintIds", []) + parcel_envelope["blockingCodes"]
        ))

    return {
        "schemaVersion": FEASIBILITY_FROM_PROGRAM_SCHEMA,
        "feasibilityInput": feasibility_input,
        "productRevenue": items,
        "assumptions": assumptions,
        "landAreaSqm": land_area,
        "grossFloorAreaSqm": gfa,
        "programAreaBasis": {key: basis for key, basis in (("land", land_basis), ("construction", construction_basis))
                             if basis is not None},
        "buildableEnvelope": parcel_envelope,
        "scenarioEligibility": scenario_eligibility,
        "comparablePricePerPy": comparable,
        "developmentProfit": profit,
        "profitMargin": margin,
        "needsReview": bool(assumptions) or not scenario_eligibility.get("reportGrade"),
        "hasRevenueAndCost": bool(feasibility_input.get("total_revenue") and feasibility_input.get("total_cost")),
        "note": (
            "파싱된 상품구성·연면적·공시지가·시장 비교가에서 결정론적으로 구성한 사업수지 초안입니다. "
            "가격/원가 추정치는 assumptions에 명시하며 승인 전 실제 분양가·임대료·토지비·공사비로 대체해야 합니다."
        ),
    }
