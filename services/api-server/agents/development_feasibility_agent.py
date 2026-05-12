from typing import Any, Dict, List

from core.calculations import (
    calculate_development_profit,
    calculate_exit_sale_rate,
    calculate_ltc,
    calculate_ltv,
    calculate_profit_margin,
    sqm_to_py,
)


class DevelopmentFeasibilityAgent:
    def analyze(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        land = project_data.get("land", {})
        regulation = project_data.get("regulation", {})
        feasibility = project_data.get("feasibility_input", {})
        architectural_plan = project_data.get("architectural_plan", {})
        pf_structure = project_data.get("pf_structure", [])

        program = self._architectural_program(land, regulation, architectural_plan)
        revenue = self._revenue(feasibility, program, project_data.get("sales_assumptions", {}))
        costs = self._costs(feasibility, program, project_data.get("cost_assumptions", {}))
        returns = self._returns(revenue, costs)
        pf = self._pf_readiness(revenue, costs, pf_structure)
        sensitivity = self._sensitivity(revenue, costs)
        quality = self._data_quality(project_data, program, revenue, costs)

        return {
            "analysisType": "developer_and_pf_feasibility",
            "summary": self._summary(returns, pf, quality),
            "architecturalProgram": program,
            "revenue": revenue,
            "costs": costs,
            "developerReturns": returns,
            "pfReadiness": pf,
            "sensitivity": sensitivity,
            "dataQuality": quality,
        }

    def _architectural_program(
        self,
        land: Dict[str, Any],
        regulation: Dict[str, Any],
        architectural_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        land_area = land.get("area_sqm", 0)
        applied_far = regulation.get("applied_far", 0)
        applied_bcr = regulation.get("applied_bcr", 0)
        gross_floor_area = architectural_plan.get("gross_floor_area_sqm") or (
            land_area * applied_far / 100 if land_area and applied_far else 0
        )
        building_coverage = architectural_plan.get("building_coverage_sqm") or (
            land_area * applied_bcr / 100 if land_area and applied_bcr else 0
        )
        saleable_area = architectural_plan.get("saleable_area_sqm") or gross_floor_area * 0.72
        efficiency = saleable_area / gross_floor_area if gross_floor_area else 0

        return {
            "landAreaSqm": land_area,
            "landAreaPy": sqm_to_py(land_area),
            "appliedFar": applied_far,
            "appliedBcr": applied_bcr,
            "grossFloorAreaSqm": gross_floor_area,
            "grossFloorAreaPy": sqm_to_py(gross_floor_area),
            "buildingCoverageSqm": building_coverage,
            "saleableAreaSqm": saleable_area,
            "saleableAreaPy": sqm_to_py(saleable_area),
            "saleableEfficiency": efficiency,
        }

    def _revenue(
        self,
        feasibility: Dict[str, Any],
        program: Dict[str, Any],
        sales_assumptions: Dict[str, Any],
    ) -> Dict[str, Any]:
        total_revenue = feasibility.get("total_revenue", 0)
        other_revenue = sales_assumptions.get("other_revenue", 0)
        saleable_area_sqm = program["saleableAreaSqm"]
        avg_price_per_sqm = sales_assumptions.get("avg_price_per_sqm")
        if not total_revenue and avg_price_per_sqm and saleable_area_sqm:
            total_revenue = avg_price_per_sqm * saleable_area_sqm + other_revenue
        if not avg_price_per_sqm and saleable_area_sqm:
            avg_price_per_sqm = max(0, total_revenue - other_revenue) / saleable_area_sqm

        return {
            "salesRevenue": max(0, total_revenue - other_revenue),
            "otherRevenue": other_revenue,
            "totalRevenue": total_revenue,
            "avgPricePerSqm": avg_price_per_sqm or 0,
            "avgPricePerPy": (avg_price_per_sqm or 0) / 0.3025 if avg_price_per_sqm else 0,
        }

    def _costs(
        self,
        feasibility: Dict[str, Any],
        program: Dict[str, Any],
        cost_assumptions: Dict[str, Any],
    ) -> Dict[str, Any]:
        gross_floor_area_sqm = program["grossFloorAreaSqm"]
        construction_cost = feasibility.get("construction_cost", 0)
        cost_per_sqm = cost_assumptions.get("construction_cost_per_sqm")
        if not construction_cost and cost_per_sqm and gross_floor_area_sqm:
            construction_cost = cost_per_sqm * gross_floor_area_sqm

        land_cost = feasibility.get("land_cost", 0)
        finance_cost = feasibility.get("finance_cost", 0)
        other_cost = feasibility.get("other_cost", 0)
        total_cost = feasibility.get("total_cost") or land_cost + construction_cost + finance_cost + other_cost

        return {
            "landCost": land_cost,
            "constructionCost": construction_cost,
            "financeCost": finance_cost,
            "otherCost": other_cost,
            "totalCost": total_cost,
            "constructionCostPerSqm": construction_cost / gross_floor_area_sqm if gross_floor_area_sqm else 0,
            "constructionCostPerPy": construction_cost / program["grossFloorAreaPy"] if program["grossFloorAreaPy"] else 0,
            "costRatio": {
                "land": land_cost / total_cost if total_cost else 0,
                "construction": construction_cost / total_cost if total_cost else 0,
                "finance": finance_cost / total_cost if total_cost else 0,
                "other": other_cost / total_cost if total_cost else 0,
            },
        }

    def _returns(self, revenue: Dict[str, Any], costs: Dict[str, Any]) -> Dict[str, Any]:
        profit = calculate_development_profit(revenue["totalRevenue"], costs["totalCost"])
        return {
            "developmentProfit": profit,
            "profitMargin": calculate_profit_margin(profit, revenue["totalRevenue"]),
            "profitOnCost": profit / costs["totalCost"] if costs["totalCost"] else 0,
            "breakEvenRevenue": costs["totalCost"],
            "breakEvenSaleRate": costs["totalCost"] / revenue["totalRevenue"] if revenue["totalRevenue"] else 0,
        }

    def _pf_readiness(
        self,
        revenue: Dict[str, Any],
        costs: Dict[str, Any],
        pf_structure: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        total_pf = sum(t.get("amount", 0) for t in pf_structure)
        total_revenue = revenue["totalRevenue"]
        total_cost = costs["totalCost"]
        recommended_by_revenue = total_revenue * 0.65
        recommended_by_cost = total_cost * 0.75
        recommended_pf_limit = min(recommended_by_revenue, recommended_by_cost) if total_revenue and total_cost else 0
        return {
            "totalPfAmount": total_pf,
            "ltvByRevenue": calculate_ltv(total_pf, total_revenue),
            "ltcByCost": calculate_ltc(total_pf, total_cost),
            "exitSaleRate": calculate_exit_sale_rate(total_pf, costs["financeCost"], total_revenue),
            "requiredEquity": max(0, total_cost - total_pf),
            "recommendedPfLimit": recommended_pf_limit,
            "headroomToRecommendedLimit": recommended_pf_limit - total_pf,
            "status": "ready" if total_pf <= recommended_pf_limit or not recommended_pf_limit else "leverage_review_required",
        }

    def _sensitivity(self, revenue: Dict[str, Any], costs: Dict[str, Any]) -> List[Dict[str, Any]]:
        scenarios = [
            ("base", 1.0, 1.0),
            ("sales_minus_5pct", 0.95, 1.0),
            ("sales_minus_10pct", 0.90, 1.0),
            ("construction_plus_5pct", 1.0, 1.05),
            ("combined_downside", 0.90, 1.05),
        ]
        rows = []
        base_construction = costs["constructionCost"]
        non_construction_cost = costs["totalCost"] - base_construction
        for name, revenue_factor, construction_factor in scenarios:
            scenario_revenue = revenue["totalRevenue"] * revenue_factor
            scenario_cost = non_construction_cost + base_construction * construction_factor
            profit = scenario_revenue - scenario_cost
            rows.append({
                "scenario": name,
                "totalRevenue": scenario_revenue,
                "totalCost": scenario_cost,
                "developmentProfit": profit,
                "profitMargin": calculate_profit_margin(profit, scenario_revenue),
            })
        return rows

    def _data_quality(
        self,
        project_data: Dict[str, Any],
        program: Dict[str, Any],
        revenue: Dict[str, Any],
        costs: Dict[str, Any],
    ) -> Dict[str, Any]:
        missing = []
        if not project_data.get("land", {}).get("area_sqm"):
            missing.append("land.area_sqm")
        if not program["grossFloorAreaSqm"]:
            missing.append("architectural_plan.gross_floor_area_sqm or regulation.applied_far")
        if not revenue["totalRevenue"]:
            missing.append("feasibility_input.total_revenue or sales_assumptions.avg_price_per_sqm")
        if not costs["totalCost"]:
            missing.append("feasibility_input.total_cost or cost breakdown")
        if not project_data.get("pf_structure"):
            missing.append("pf_structure")
        return {
            "missingFields": missing,
            "confidence": max(0.2, 1 - len(missing) * 0.15),
            "source": project_data.get("_meta", {}).get("parsing_source", "project_data"),
            "warnings": [f"{field} 자료 보강 필요" for field in missing],
        }

    def _summary(self, returns: Dict[str, Any], pf: Dict[str, Any], quality: Dict[str, Any]) -> str:
        margin = returns["profitMargin"]
        pf_status = pf["status"]
        if quality["missingFields"]:
            return "핵심 입력값 일부가 누락되어 예비 사업수지분석으로 산출되었습니다."
        if margin >= 0.15 and pf_status == "ready":
            return "디벨로퍼 관점의 사업수지와 PF 조달 안정성이 모두 양호한 편입니다."
        if margin >= 0.08:
            return "사업수지는 보통 수준이며 PF 레버리지와 민감도 조건 재점검이 필요합니다."
        return "사업수지 여력이 낮아 분양가, 공사비, 토지비, PF 규모 재검토가 필요합니다."
