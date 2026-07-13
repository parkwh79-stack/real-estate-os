"""Product Planning engine (deterministic).

Given a land parcel + zoning (면적, 용적률/건폐율) it derives an indicative
development **program per asset type** — 연면적, 전용면적, 세대수/호실수, 전용률,
주차대수, 분양가/분양수입, 임대료/NOI — so REALYTICS can compare what to build on
the same land (아파트 vs 오피스텔 vs 상가 vs 지산 vs 물류 vs 생숙).

Invariants (AGENTS.md §1):
- Deterministic: every number is arithmetic from land/zoning + planning parameters.
  No LLM produces a number here (an LLM may *review* the program as a Pod reviewer).
- No fabrication: standard planning ratios (전용률, 세대 평형, 주차원단위) are
  labelled ``basis=planning_benchmark`` — they are indicative planning defaults, not
  deal evidence. Caller-supplied assumptions (실제 분양가·전용률·평형) override them.
  Revenue/rent that has no price basis is left null and flagged, never invented.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.calculations import sqm_to_py

PRODUCT_PLANNING_SCHEMA_VERSION = "realytics-product-planning-v1"

SQM_PER_PY = 1 / 0.3025  # ≈ 3.3058 ㎡/평

# Indicative planning benchmarks by asset type. NOT deal evidence — clearly
# labelled and overridable. Sources: typical Korean development planning norms.
#   efficiency        전용률 (전용/연면적)
#   avgUnitExclusivePy 세대/호실 평균 전용면적(평)
#   parkingPerUnit    세대/호실당 주차대수 (unit-based) — used when applicable
#   unitBasis         "household"(세대) | "room"(호실) | "gfa"(면적기반, 세대 미산정)
#   incomeAsset       임대/운영형 자산 여부(임대료·Cap Rate 축 사용)
PLANNING_BENCHMARKS: Dict[str, Dict[str, Any]] = {
    "apartment": {"label": "아파트", "efficiency": 0.75, "avgUnitExclusivePy": 25.7, "parkingPerUnit": 1.2, "unitBasis": "household", "incomeAsset": False},
    "officetel": {"label": "오피스텔", "efficiency": 0.50, "avgUnitExclusivePy": 7.6, "parkingPerUnit": 0.6, "unitBasis": "room", "incomeAsset": False},
    "commercial": {"label": "상업시설", "efficiency": 0.55, "avgUnitExclusivePy": 15.0, "parkingPerUnit": 0.0, "unitBasis": "room", "incomeAsset": True},
    "knowledge_industry_center": {"label": "지식산업센터", "efficiency": 0.60, "avgUnitExclusivePy": 30.0, "parkingPerUnit": 0.0, "unitBasis": "room", "incomeAsset": False},
    "mixed_use": {"label": "복합개발", "efficiency": 0.65, "avgUnitExclusivePy": 18.0, "parkingPerUnit": 1.0, "unitBasis": "household", "incomeAsset": False},
    "logistics": {"label": "물류센터", "efficiency": 0.92, "avgUnitExclusivePy": 0.0, "parkingPerUnit": 0.0, "unitBasis": "gfa", "incomeAsset": True},
    "lodging": {"label": "생활형숙박시설", "efficiency": 0.45, "avgUnitExclusivePy": 8.0, "parkingPerUnit": 0.3, "unitBasis": "room", "incomeAsset": True},
    "other": {"label": "기타", "efficiency": 0.60, "avgUnitExclusivePy": 0.0, "parkingPerUnit": 0.0, "unitBasis": "gfa", "incomeAsset": False},
}

# GFA-based statutory parking fallback (1 space per N ㎡ of GFA) when no unit basis.
_PARKING_SQM_PER_SPACE = {
    "commercial": 134.0,
    "knowledge_industry_center": 150.0,
    "logistics": 500.0,
    "other": 200.0,
}


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # drop NaN


def _benchmark(asset_type: str) -> Dict[str, Any]:
    return PLANNING_BENCHMARKS.get(asset_type, PLANNING_BENCHMARKS["other"])


class ProductPlanningAgent:
    def plan_asset(
        self,
        *,
        asset_type: str,
        land_area_sqm: float,
        applied_far: float,
        applied_bcr: float = 0.0,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Derive an indicative program for one asset type on the given land."""
        overrides = overrides or {}
        bench = _benchmark(asset_type)
        notes: List[str] = []
        benchmarks_used: List[str] = []

        gross_floor_area_sqm = _num(overrides.get("gross_floor_area_sqm"))
        if gross_floor_area_sqm is None:
            gross_floor_area_sqm = land_area_sqm * applied_far / 100 if (land_area_sqm and applied_far) else 0.0
        building_coverage_sqm = land_area_sqm * applied_bcr / 100 if (land_area_sqm and applied_bcr) else 0.0

        efficiency = _num(overrides.get("efficiency"))
        if efficiency is None:
            efficiency = bench["efficiency"]
            benchmarks_used.append("efficiency")
        exclusive_area_sqm = gross_floor_area_sqm * efficiency

        # Units / rooms
        unit_basis = bench["unitBasis"]
        avg_unit_exclusive_py = _num(overrides.get("avg_unit_exclusive_py"))
        if avg_unit_exclusive_py is None:
            avg_unit_exclusive_py = bench["avgUnitExclusivePy"]
            if unit_basis in {"household", "room"}:
                benchmarks_used.append("avgUnitExclusivePy")
        unit_count = None
        if unit_basis in {"household", "room"} and avg_unit_exclusive_py and exclusive_area_sqm:
            unit_count = int(sqm_to_py(exclusive_area_sqm) / avg_unit_exclusive_py)

        # Parking
        parking_spaces = None
        parking_per_unit = _num(overrides.get("parking_per_unit"))
        if parking_per_unit is None:
            parking_per_unit = bench["parkingPerUnit"]
        if unit_count and parking_per_unit:
            parking_spaces = round(unit_count * parking_per_unit)
            benchmarks_used.append("parkingPerUnit")
        elif gross_floor_area_sqm:
            per_space = _PARKING_SQM_PER_SPACE.get(asset_type)
            if per_space:
                parking_spaces = round(gross_floor_area_sqm / per_space)
                benchmarks_used.append("parkingStatutoryRatio")

        # Revenue (분양) — only when a price basis is supplied (no fabrication).
        price_per_py = _num(overrides.get("price_per_py")) or _num(overrides.get("avg_sale_price_per_py"))
        # 분양(계약)면적 ≈ 전용 / 전용률 (공용 포함). Contract area py for pricing.
        contract_area_py = sqm_to_py(exclusive_area_sqm) / efficiency if (exclusive_area_sqm and efficiency) else 0.0
        sales_revenue = None
        if price_per_py and contract_area_py:
            sales_revenue = round(price_per_py * contract_area_py)
        else:
            notes.append("분양가(평당) 미입력 — 분양수입 미산정")

        # Rent / NOI (임대·운영형 자산)
        rent_per_py_month = _num(overrides.get("rent_per_py_month"))
        cap_rate = _num(overrides.get("cap_rate"))
        annual_noi = None
        income_value = None
        if bench["incomeAsset"] and rent_per_py_month and contract_area_py:
            occupancy = _num(overrides.get("occupancy")) or 0.9
            annual_noi = round(rent_per_py_month * contract_area_py * 12 * occupancy)
            if cap_rate:
                income_value = round(annual_noi / cap_rate)
        elif bench["incomeAsset"]:
            notes.append("임대료(평당·월) 미입력 — NOI/수익환원가 미산정")

        return {
            "assetType": asset_type,
            "assetTypeLabel": bench["label"],
            "unitBasis": unit_basis,
            "incomeAsset": bench["incomeAsset"],
            "landAreaSqm": land_area_sqm,
            "appliedFar": applied_far,
            "appliedBcr": applied_bcr,
            "grossFloorAreaSqm": round(gross_floor_area_sqm),
            "grossFloorAreaPy": round(sqm_to_py(gross_floor_area_sqm)),
            "buildingCoverageSqm": round(building_coverage_sqm),
            "exclusiveAreaSqm": round(exclusive_area_sqm),
            "exclusiveAreaPy": round(sqm_to_py(exclusive_area_sqm)),
            "efficiency": efficiency,
            "avgUnitExclusivePy": avg_unit_exclusive_py or None,
            "unitCount": unit_count,
            "parkingSpaces": parking_spaces,
            "pricePerPy": price_per_py,
            "contractAreaPy": round(contract_area_py) if contract_area_py else None,
            "salesRevenue": sales_revenue,
            "rentPerPyMonth": rent_per_py_month,
            "capRate": cap_rate,
            "annualNoi": annual_noi,
            "incomeValue": income_value,
            "benchmarksUsed": sorted(set(benchmarks_used)),
            "assumptionBasis": "planning_benchmark" if benchmarks_used else "user_input",
            "notes": notes,
            "sourceLocator": {"engineKey": "product_planning", "field": "land+regulation", "sourceType": "calculation"},
        }

    def compare(
        self,
        *,
        land_area_sqm: float,
        applied_far: float,
        applied_bcr: float = 0.0,
        scenarios: List[Dict[str, Any]],
        project_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for scenario in scenarios:
            asset_type = scenario.get("assetType") or "other"
            rows.append(
                self.plan_asset(
                    asset_type=asset_type,
                    land_area_sqm=land_area_sqm,
                    applied_far=applied_far,
                    applied_bcr=applied_bcr,
                    overrides=scenario.get("overrides") if isinstance(scenario.get("overrides"), dict) else {},
                )
            )
        priced = [r for r in rows if r.get("salesRevenue") or r.get("incomeValue")]
        top_by_value = None
        if priced:
            top = max(priced, key=lambda r: (r.get("salesRevenue") or r.get("incomeValue") or 0))
            top_by_value = {
                "assetType": top["assetType"],
                "assetTypeLabel": top["assetTypeLabel"],
                "value": top.get("salesRevenue") or top.get("incomeValue"),
                "valueKind": "salesRevenue" if top.get("salesRevenue") else "incomeValue",
                "note": "입력 가정 기준 최고 산출가치 상품유형입니다. 분양가·임대료 가정에 따라 달라집니다.",
            }
        return {
            "schemaVersion": PRODUCT_PLANNING_SCHEMA_VERSION,
            "projectName": project_name,
            "landAreaSqm": land_area_sqm,
            "appliedFar": applied_far,
            "appliedBcr": applied_bcr,
            "scenarioCount": len(rows),
            "pricedCount": len(priced),
            "rows": rows,
            "topByValue": top_by_value,
            "notes": (
                "동일 토지·규제 조건의 자산유형별 상품기획(면적·세대/호실·전용률·주차·분양가·임대료)입니다. "
                "전용률·평형·주차원단위는 계획 벤치마크(basis=planning_benchmark)이며 실제 가정 입력 시 대체됩니다. "
                "가격 근거가 없는 항목은 산정하지 않습니다."
            ),
        }
