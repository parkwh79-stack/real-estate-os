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

from typing import Any, Dict, List, Optional

FEASIBILITY_FROM_PROGRAM_SCHEMA = "realytics-feasibility-from-program-v1"

DEFAULT_CONSTRUCTION_COST_PER_SQM = 3_000_000  # 연면적당 공사비 벤치마크(원/㎡)
DEFAULT_RENTAL_SALE_FACTOR = 0.7  # 공공지원민간임대 매각/분양전환가 = 분양가 × factor (가정)
_RENTAL_MARKERS = ("임대", "공공")


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        n = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return n if n == n and n > 0 else None


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


def _regulation_from_parcels(project_data: Dict[str, Any]) -> tuple:
    """Aggregate parcel VWorld landUsePlanning into a (legalFar, [용도지역]) — the
    missing bridge from per-parcel land-use attributes to the feasibility engine.
    Returns (None, zones) when VWorld did not provide a legal FAR (e.g. 계획관리지역
    원형지) so the pod honestly stays blocked instead of using a fabricated FAR."""
    ledger = project_data.get("landLedger") or project_data.get("land_ledger") or {}
    parcels = ledger.get("parcels") if isinstance(ledger, dict) else None
    if not isinstance(parcels, list):
        parcels = project_data.get("land_acquisition") or []
    fars: List[float] = []
    zones: List[str] = []
    for parcel in parcels:
        if not isinstance(parcel, dict):
            continue
        lup = parcel.get("landUsePlanning") or {}
        if not isinstance(lup, dict):
            continue
        far = _num(lup.get("legalFar"))
        if far:
            fars.append(far)
        for zone in lup.get("landUseZones") or []:
            if zone and zone not in zones:
                zones.append(str(zone))
    # Use the max legal FAR across parcels (the governing 용도지역) when available.
    return (max(fars) if fars else None, zones)


def _parcel_land_cost(project_data: Dict[str, Any]) -> tuple:
    """Sum (공시지가 × 면적) over the 토지조서 parcels. Returns (cost, priced_count, total)."""
    ledger = project_data.get("landLedger") or project_data.get("land_ledger") or {}
    parcels = ledger.get("parcels") if isinstance(ledger, dict) else None
    if not isinstance(parcels, list) or not parcels:
        parcels = project_data.get("land_acquisition") or []
    total = 0
    priced = 0
    cost = 0.0
    for parcel in parcels:
        if not isinstance(parcel, dict):
            continue
        total += 1
        area = _num(parcel.get("areaSqm") or parcel.get("area_sqm"))
        price = _num(parcel.get("officialLandPricePerSqm"))
        if area and price:
            cost += area * price
            priced += 1
    return (round(cost) if cost else None, priced, total)


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
) -> Dict[str, Any]:
    options = options or {}
    assumptions: List[str] = []

    land_area = _num(options.get("land_area_sqm")) or _land_area(project_data, parsed_facts)
    gfa = _num((project_data.get("architectural_plan") or {}).get("gross_floor_area_sqm"))
    if not gfa and land_area:  # fall back to 연면적 = 토지면적 × 용적률
        far = _num(options.get("applied_far")) or _num((project_data.get("regulation") or {}).get("applied_far"))
        if not far:  # wiring: pull legalFar from parcel VWorld landUsePlanning if VWorld provided it
            far, zones = _regulation_from_parcels(project_data)
            if far:
                assumptions.append(f"용적률 {far:g}% = 필지 용도지역({', '.join(zones[:2])}) 법정 용적률(VWorld) 적용.")
        if far:
            gfa = land_area * far / 100
    comparable = _num(options.get("sale_price_per_py")) or _comparable_price_per_py(project_data, parsed_facts)
    rental_factor = _num(options.get("rental_sale_factor")) or DEFAULT_RENTAL_SALE_FACTOR

    # --- Revenue from product mix ---
    product_mix = _product_mix(project_data)
    items: List[Dict[str, Any]] = []
    total_revenue = 0.0
    for pm in product_mix:
        units = _num(pm.get("units"))
        contract = _num(pm.get("contractAreaPy") or pm.get("contract_area_py"))
        label = str(pm.get("type") or pm.get("productType") or "")
        is_rental = any(marker in label for marker in _RENTAL_MARKERS)
        price = _num(pm.get("pricePerPy") or pm.get("price_per_py"))
        price_source = "parsed" if price else None
        if not price and comparable:
            price = round(comparable * (rental_factor if is_rental else 1.0))
            price_source = "rental_conversion_estimate" if is_rental else "market_comparable_estimate"
        if not (units and contract and price):
            items.append({"type": label, "units": int(units) if units else None, "status": "no_price"})
            continue
        revenue = price * contract * units
        total_revenue += revenue
        items.append({
            "type": label,
            "units": int(units),
            "contractAreaPy": contract,
            "pricePerPy": round(price),
            "isRental": is_rental,
            "priceSource": price_source,
            "revenue": round(revenue),
        })
    if comparable and any(i.get("priceSource", "").endswith("estimate") for i in items):
        assumptions.append(
            f"평당가 미기재 → 일반분양 = 시장 비교사례 {round(comparable/10000):,}만원/평, "
            f"임대(공공지원민간임대·공공임대) = 그 {rental_factor:g}배로 추정(매각/전환가 가정) — 실제 분양가·임대료 확인 필요."
        )

    # --- Costs ---
    land_cost = _num(options.get("land_cost"))
    if not land_cost:
        # Prefer a per-parcel 공시지가 sum (토지조서) — more accurate than aggregate.
        parcel_cost, parcel_priced, parcel_total = _parcel_land_cost(project_data)
        if parcel_cost:
            land_cost = parcel_cost
            assumptions.append(
                f"토지비 = 토지조서 {parcel_priced}/{parcel_total}필지 공시지가×면적 합 {round(parcel_cost/1e8):,}억원(하한 추정) — "
                "실제 매입가(수용보상가)는 공시지가보다 높음; 미평가 필지 보완 필요."
            )
        else:
            land_price = _official_land_price(project_data, parsed_facts)
            if land_price and land_area:
                land_cost = land_price * land_area
                assumptions.append(
                    f"토지비 = 공시지가 {round(land_price/10000):,}만원/㎡ × {round(land_area):,}㎡ 추정 — 실제 토지 매입가 확인 필요(통상 공시지가보다 높음)."
                )
    construction_cost = _num(options.get("construction_cost"))
    ccps = _num(options.get("construction_cost_per_sqm")) or DEFAULT_CONSTRUCTION_COST_PER_SQM
    if not construction_cost and gfa:
        construction_cost = gfa * ccps
        assumptions.append(f"공사비 = 연면적 {round(gfa):,}㎡ × {round(ccps/10000):,}만원/㎡ 벤치마크 — 실제 도급단가 확인 필요.")
    finance_cost = _num(options.get("finance_cost")) or 0.0
    cost_parts = [c for c in (land_cost, construction_cost, finance_cost) if c]
    total_cost = sum(cost_parts) if cost_parts else None

    feasibility_input: Dict[str, Any] = {}
    if total_revenue > 0:
        feasibility_input["total_revenue"] = round(total_revenue)
    if land_cost:
        feasibility_input["land_cost"] = round(land_cost)
    if construction_cost:
        feasibility_input["construction_cost"] = round(construction_cost)
    if finance_cost:
        feasibility_input["finance_cost"] = round(finance_cost)
    if total_cost:
        feasibility_input["total_cost"] = round(total_cost)

    profit = None
    margin = None
    if feasibility_input.get("total_revenue") and feasibility_input.get("total_cost"):
        profit = feasibility_input["total_revenue"] - feasibility_input["total_cost"]
        margin = round(profit / feasibility_input["total_revenue"], 4)

    return {
        "schemaVersion": FEASIBILITY_FROM_PROGRAM_SCHEMA,
        "feasibilityInput": feasibility_input,
        "productRevenue": items,
        "assumptions": assumptions,
        "landAreaSqm": land_area,
        "grossFloorAreaSqm": gfa,
        "comparablePricePerPy": comparable,
        "developmentProfit": profit,
        "profitMargin": margin,
        "needsReview": bool(assumptions),
        "hasRevenueAndCost": bool(feasibility_input.get("total_revenue") and feasibility_input.get("total_cost")),
        "note": (
            "파싱된 상품구성·연면적·공시지가·시장 비교가에서 결정론적으로 구성한 사업수지 초안입니다. "
            "가격/원가 추정치는 assumptions에 명시하며 승인 전 실제 분양가·임대료·토지비·공사비로 대체해야 합니다."
        ),
    }
