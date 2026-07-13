"""Build/sync feasibility_input from parsed 사업수지 sources — AI-assisted.

The cash-flow / PF engines read ``feasibility_input`` (total_revenue, total_cost,
construction_cost, land_cost, finance_cost, pf_amount). When a project only has a
parsed 사업수지 (canonical ``feasibilityStudyV2`` from a workbook, or parsed_fact
records) but ``feasibility_input`` is empty, the cash flow shows all zeros.

This use case pulls those values into a ``feasibility_input`` draft, in priority:
  1. existing non-zero feasibility_input (kept)
  2. canonical feasibilityStudyV2.metrics (workbook import)     — high confidence
  3. parsed_fact records (feasibility_input.*)                   — parsed document
  4. deterministic derivation from product planning + market price + land 공시지가
     + construction benchmark                                    — needs review

Invariants: every derived value carries a source + confidence and is flagged
``needsReview`` (candidate) unless it came from a real parsed source. Missing
values stay missing (listed in ``missing``) — never fabricated to a made-up number.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

FEASIBILITY_INPUT_BUILDER_SCHEMA = "realytics-feasibility-input-draft-v1"

FIELDS = ["total_revenue", "total_cost", "construction_cost", "land_cost", "finance_cost", "pf_amount"]

# Construction cost benchmark (KRW / ㎡ GFA) when no cost basis exists — clearly a
# planning benchmark, flagged for review, never treated as evidence.
_CONSTRUCTION_COST_PER_SQM_BENCHMARK = 3_500_000


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        n = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return n if n == n and n > 0 else None


def _derive_from_program(project_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Deterministically propose revenue/cost from land + regulation + product plan +
    market price + 공시지가 (the "AI와 함께 사업수지 만들기" fallback). All flagged needsReview."""
    out: Dict[str, Dict[str, Any]] = {}
    land = project_data.get("land") or {}
    regulation = project_data.get("regulation") or {}
    land_area = _num(land.get("area_sqm"))
    far = _num(regulation.get("applied_far"))

    # Revenue: prefer explicit product_mix revenue, else land×FAR×전용률×분양가(median).
    product_mix = project_data.get("product_mix") or project_data.get("productMix") or []
    mix_revenue = 0.0
    for item in product_mix if isinstance(product_mix, list) else []:
        if not isinstance(item, dict):
            continue
        rev = _num(item.get("totalRevenue") or item.get("total_revenue"))
        if rev:
            mix_revenue += rev
    if mix_revenue > 0:
        out["total_revenue"] = {"value": round(mix_revenue), "source": "product_mix_revenue", "confidence": 0.55, "needsReview": True}
    elif land_area and far:
        # 연면적 × 전용률(0.7) × 분양가(평당) — 분양가는 시장 median 있으면 사용.
        price_per_py = _num((project_data.get("market") or {}).get("proposedPricePerPy")) or _num(
            (project_data.get("marketProductDepth") or {}).get("publicTransactionMedianPerPy")
        )
        if price_per_py:
            gfa = land_area * far / 100
            contract_area_py = (gfa * 0.7) * 0.3025 / 0.7  # 전용→계약 환산 근사
            out["total_revenue"] = {
                "value": round(price_per_py * contract_area_py),
                "source": "derived_land_far_market_price",
                "confidence": 0.4,
                "needsReview": True,
            }

    # Land cost: 공시지가 × 토지면적 (공시지가는 실제 매입가와 다를 수 있어 needsReview).
    jiga = _num(land.get("officialLandPricePerSqm"))
    if jiga and land_area:
        out["land_cost"] = {"value": round(jiga * land_area), "source": "official_land_price_x_area", "confidence": 0.4, "needsReview": True}

    # Construction cost: 연면적 × 벤치마크 단가.
    if land_area and far:
        gfa = land_area * far / 100
        out["construction_cost"] = {
            "value": round(gfa * _CONSTRUCTION_COST_PER_SQM_BENCHMARK),
            "source": "gfa_x_construction_benchmark",
            "confidence": 0.35,
            "needsReview": True,
        }
    return out


def build_feasibility_input_draft(
    project_data: Dict[str, Any],
    *,
    parsed_facts: Optional[List[Dict[str, Any]]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    existing = project_data.get("feasibility_input") or {}
    v2 = project_data.get("feasibilityStudyV2") or {}
    v2_metrics = (v2.get("metrics") or {}) if isinstance(v2, dict) else {}

    fields: Dict[str, Any] = {}
    sources: Dict[str, Dict[str, Any]] = {}

    def _set(field: str, value: Any, source: str, confidence: float, needs_review: bool = False) -> None:
        num = _num(value)
        if num is None or field in fields:
            return
        fields[field] = num
        sources[field] = {"source": source, "confidence": confidence, "needsReview": needs_review}

    # 1) existing non-zero feasibility_input (kept as-is, highest trust)
    for field in FIELDS:
        _set(field, existing.get(field), "feasibility_input", 0.95)

    # 2) canonical feasibilityStudyV2 metrics (workbook import = parsed 사업수지)
    for field in FIELDS:
        _set(field, v2_metrics.get(field), "feasibility_workbook_v2", 0.9)

    # 3) parsed_fact records for feasibility fields. Distinguish REAL parsed document
    #    values from auto_supplement benchmark PROXIES (factOrigin=auto_supplement) —
    #    the latter are placeholders the pipeline filled when real data was missing and
    #    must not be presented as a real 사업수지 (AGENTS.md §1/§7).
    for record in parsed_facts or []:
        if not isinstance(record, dict):
            continue
        canonical = str(record.get("canonicalField") or "")
        if not canonical.startswith("feasibility_input."):
            continue
        field = canonical.split(".", 1)[1]
        if field not in FIELDS:
            continue
        is_proxy = str(record.get("factOrigin") or "") == "auto_supplement"
        _set(
            field,
            record.get("normalizedValue") or record.get("value"),
            "auto_supplement_proxy" if is_proxy else "parsed_fact",
            0.25 if is_proxy else 0.6,
            needs_review=True,
        )

    # 4) AI-assisted composition from the parsed development program (상품구성·연면적·
    #    공시지가·시장 비교가) for anything still missing — the "사업수지를 직접 만들기" path.
    program: Dict[str, Any] = {}
    if any(field not in fields for field in ("total_revenue", "total_cost")):
        from use_cases.feasibility_from_program import build_feasibility_from_program

        program = build_feasibility_from_program(project_data, parsed_facts=parsed_facts, options=options)
        for field, value in (program.get("feasibilityInput") or {}).items():
            _set(field, value, "program_composition", 0.45, needs_review=True)

    # total_cost from its parts if still missing
    if "total_cost" not in fields:
        parts = [fields.get("land_cost"), fields.get("construction_cost"), fields.get("finance_cost")]
        present = [p for p in parts if p]
        if present:
            _set("total_cost", sum(present), "sum_of_cost_parts", 0.4, needs_review=True)

    missing = [field for field in FIELDS if field not in fields]
    needs_review = any(meta.get("needsReview") for meta in sources.values())
    method = (
        "feasibility_workbook_v2" if v2_metrics
        else "parsed_fact" if any(s["source"] == "parsed_fact" for s in sources.values())
        else "derived" if fields
        else "no_source"
    )
    program_breakdown = None
    if program and (program.get("productRevenue") or program.get("assumptions")):
        program_breakdown = {
            "productRevenue": program.get("productRevenue"),
            "assumptions": program.get("assumptions"),
            "landAreaSqm": program.get("landAreaSqm"),
            "grossFloorAreaSqm": program.get("grossFloorAreaSqm"),
            "comparablePricePerPy": program.get("comparablePricePerPy"),
            "developmentProfit": program.get("developmentProfit"),
            "profitMargin": program.get("profitMargin"),
        }

    return {
        "schemaVersion": FEASIBILITY_INPUT_BUILDER_SCHEMA,
        "feasibilityInput": fields,
        "fieldSources": sources,
        "missing": missing,
        "method": method if not (program and program.get("hasRevenueAndCost") and method in ("no_source", "derived")) else "program_composition",
        "needsReview": needs_review,
        "programBreakdown": program_breakdown,
        "hasRevenueAndCost": bool(fields.get("total_revenue") and fields.get("total_cost")),
        "note": (
            "파싱된 사업수지(feasibilityStudyV2/parsed_fact)를 우선 사용하고, 없으면 토지·규제·상품기획·"
            "시장가·공시지가로 결정론 추정합니다. 추정값은 needsReview로 표기되며 승인 전 검토가 필요합니다."
        ),
    }
