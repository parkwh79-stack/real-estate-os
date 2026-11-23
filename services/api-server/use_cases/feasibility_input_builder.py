"""Build/sync feasibility_input from parsed 사업수지 sources — AI-assisted.

The cash-flow / PF engines read ``feasibility_input`` (total_revenue, total_cost,
construction_cost, land_cost, finance_cost, pf_amount). When a project only has a
parsed 사업수지 (canonical ``feasibilityStudyV2`` from a workbook, or parsed_fact
records) but ``feasibility_input`` is empty, the cash flow shows all zeros.

This use case pulls those values into a ``feasibility_input`` draft, in priority:
  1. existing non-zero feasibility_input (kept)
  2. approved canonicalFeasibilityStudyV2.metrics               — high confidence
  3. parsed_fact records (feasibility_input.*)                   — parsed document
  4. deterministic derivation from product planning + market price + land 공시지가
     + construction benchmark                                    — needs review

Invariants: every derived value carries a source + confidence and is flagged
``needsReview`` (candidate) unless it came from a real parsed source. Missing
values stay missing (listed in ``missing``) — never fabricated to a made-up number.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from use_cases.approved_workbook_authority import (
    has_authoritative_workbook_snapshot_binding,
)

FEASIBILITY_INPUT_BUILDER_SCHEMA = "realytics-feasibility-input-draft-v1"

FIELDS = ["total_revenue", "total_cost", "construction_cost", "land_cost", "finance_cost", "pf_amount"]

PROGRAM_COST_FIELDS = {"land_cost", "construction_cost", "total_cost"}
PROGRAM_COST_KEYS = {"land_cost": "land", "construction_cost": "construction"}


def canonical_product_mix_to_runtime(product_mix: Any) -> List[Dict[str, Any]]:
    """Convert approved FeasibilityStudyV2 product rows to the runtime shape.

    Workbook imports use snake_case and nested FeasibilityValue objects, while
    the existing feasibility/cash-flow engines consume the compact product mix
    shape. Keeping this conversion explicit prevents an approved workbook from
    disappearing between the canonical store and downstream Pods.
    """
    if not isinstance(product_mix, list):
        return []

    def scalar(value: Any) -> Any:
        if isinstance(value, dict) and "value" in value:
            return value.get("value")
        return value

    def numeric(value: Any) -> Any:
        value = scalar(value)
        if value in (None, ""):
            return None
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    rows: List[Dict[str, Any]] = []
    for index, item in enumerate(product_mix, start=1):
        if not isinstance(item, dict):
            continue
        units = numeric(item.get("unit_count"))
        contract_sqm = numeric(item.get("contract_area_sqm"))
        contract_py = scalar(item.get("contract_area_py"))
        if contract_py in (None, "") and contract_sqm not in (None, ""):
            contract_py = contract_sqm * 0.3025
        else:
            contract_py = numeric(contract_py)
        price_py = numeric(item.get("avg_sale_price_per_py"))
        price_sqm = numeric(item.get("avg_sale_price_per_sqm"))
        if price_py in (None, "") and price_sqm not in (None, ""):
            price_py = price_sqm / 0.3025
        revenue = numeric(item.get("revenue"))
        row = {
            "key": item.get("key") or f"product-{index}",
            "type": item.get("product_name") or item.get("asset_type") or f"Product {index}",
            "productType": item.get("asset_type") or item.get("product_name"),
            "units": units,
            "contractAreaPy": contract_py,
            "pricePerPy": price_py,
            "totalRevenue": revenue,
            "source": "canonical_feasibility_study_v2",
            "provenance": item.get("provenance"),
        }
        rows.append({key: value for key, value in row.items() if value is not None})
    return rows


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        n = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return n if n == n and n > 0 else None


def build_feasibility_input_draft(
    project_data: Dict[str, Any],
    *,
    parsed_facts: Optional[List[Dict[str, Any]]] = None,
    options: Optional[Dict[str, Any]] = None,
    drawing: dict | None = None,
) -> Dict[str, Any]:
    existing = project_data.get("feasibility_input") or {}
    v2 = _approved_feasibility_study(project_data)
    v2_metrics = (v2.get("metrics") or {}) if isinstance(v2, dict) else {}

    fields: Dict[str, Any] = {}
    sources: Dict[str, Dict[str, Any]] = {}

    def _set(field: str, value: Any, source: str, confidence: float, needs_review: bool = False) -> None:
        num = _num(value)
        if num is None or field in fields:
            return
        fields[field] = num
        sources[field] = {"source": source, "confidence": confidence, "needsReview": needs_review}

    # 1) Explicitly approved canonical FeasibilityStudyV2 metrics = AUTHORITATIVE.
    #    This must win over any existing feasibility_input, because the latter can hold
    #    stale auto_supplement placeholders (e.g. 751 had fake 750/600억 shadowing the
    #    real 8,540/7,794억 approved workbook). The approved workbook IS the 사업수지.
    for field in FIELDS:
        _set(field, v2_metrics.get(field), "feasibility_workbook_v2", 0.92)

    # 2) existing feasibility_input for any field the workbook does not cover
    previous_sources = project_data.get("feasibility_input_sources") or {}
    refreshed_fields = set()
    for field in FIELDS:
        previous = previous_sources.get(field) or {}
        if (field in PROGRAM_COST_FIELDS and field not in fields and previous.get("source") == "program_composition"
                and _num(previous.get("value")) == _num(existing.get(field)) and _num(existing.get(field)) is not None):
            refreshed_fields.add(field)
            continue
        if previous and _num(previous.get("value")) == _num(existing.get(field)):
            _set(field, existing.get(field), str(previous.get("source") or "feasibility_input"),
                 float(previous.get("confidence") or 0.6), needs_review=previous.get("needsReview") is not False)
        else:
            _set(field, existing.get(field), "feasibility_input", 0.6, needs_review=True)

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
    if drawing or refreshed_fields or any(field not in fields for field in ("total_revenue", "total_cost")):
        from use_cases.feasibility_from_program import build_feasibility_from_program

        old_construction = previous_sources.get("construction_cost") or {}
        old_basis = old_construction.get("areaBasis") or {}
        if not drawing and "construction_cost" in refreshed_fields and old_basis.get("kind") == "drawing":
            drawing = old_basis
        program_options = dict(options or {})
        if "construction_cost" in refreshed_fields and old_basis.get("unitPriceKrw") is not None:
            program_options.setdefault("construction_cost_per_sqm", old_basis["unitPriceKrw"])
        program = build_feasibility_from_program(project_data, parsed_facts=parsed_facts, options=program_options, drawing=drawing)
        for field, value in program["feasibilityInput"].items():
            if field == "total_cost" or field in fields:
                continue
            _set(field, value, "program_composition", 0.45, needs_review=True)
            basis = program["programAreaBasis"].get(PROGRAM_COST_KEYS.get(field))
            if basis and field in sources:
                sources[field]["areaBasis"] = basis

    if "total_cost" not in fields:
        parts = [fields.get("land_cost"), fields.get("construction_cost"), fields.get("finance_cost")]
        present = [p for p in parts if p]
        if present and (not program or all(parts[:2])):
            total = sum(present) + (_num(existing.get("other_cost")) or 0)
            _set("total_cost", total, "program_composition" if program else "sum_of_cost_parts", 0.4, needs_review=True)

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
            "programAreaBasis": program.get("programAreaBasis"),
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
        "staleProgramCosts": sorted(refreshed_fields.difference(fields)),
        "programCostsIncomplete": bool(program.get("programAreaBasis") and "total_cost" not in fields),
        "hasRevenueAndCost": bool(fields.get("total_revenue") and fields.get("total_cost")),
        "note": (
            "승인된 사업수지(canonicalFeasibilityStudyV2)와 parsed_fact를 우선 사용하고, 없으면 토지·규제·상품기획·"
            "시장가·공시지가로 결정론 추정합니다. 추정값은 needsReview로 표기되며 승인 전 검토가 필요합니다."
        ),
    }


def _approved_feasibility_study(project_data: Dict[str, Any]) -> Dict[str, Any]:
    """Return only an explicitly approved workbook study.

    ``feasibilityStudyV2`` is retained as a legacy mirror after approval, but a
    candidate draft with the same shape must never become authoritative merely
    because it exists in project JSON.
    """
    for key in ("canonicalFeasibilityStudyV2", "feasibilityStudyV2"):
        study = project_data.get(key)
        if not isinstance(study, dict):
            continue
        if has_authoritative_workbook_snapshot_binding(study):
            return study
    return {}
