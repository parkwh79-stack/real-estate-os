from typing import Any, Dict, List

from agents.cash_flow_engine import run_cash_flow_engine
from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study
from core.calculations import (
    calculate_development_profit,
    calculate_exit_sale_rate,
    calculate_ltc,
    calculate_sales_value_ltv,
    calculate_profit_margin,
    sqm_to_py,
)
from core.logistics import (
    assess_logistics_risks,
    calculate_appraisal_ltv,
    calculate_exit_value,
    calculate_forward_sale_ltv,
    calculate_location_score,
    calculate_logistics_noi,
    calculate_spec_adequacy_score,
    calculate_tenant_stability_score,
)
from schemas.feasibility_study import FEASIBILITY_STUDY_SCHEMA_VERSION, FEASIBILITY_WORKBOOK_VERSION


ASSET_CALCULATION_PROFILES = {
    "apartment": {
        "revenueMode": "presale",
        "defaultCollection": "10-60-30",
        "recoveryHaircut": 0.10,
        "ltvRiskThreshold": 0.85,
    },
    "officetel": {
        "revenueMode": "presale_or_rent_yield_check",
        "defaultCollection": "10-60-30",
        "recoveryHaircut": 0.20,
        "ltvRiskThreshold": 0.80,
    },
    "commercial": {
        "revenueMode": "presale_and_rent_cap_check",
        "defaultCollection": "10-50-40",
        "recoveryHaircut": 0.30,
        "ltvRiskThreshold": 0.75,
    },
    "knowledge_industry_center": {
        "revenueMode": "presale_and_lease_demand_check",
        "defaultCollection": "10-50-40",
        "recoveryHaircut": 0.25,
        "ltvRiskThreshold": 0.75,
    },
    "mixed_use": {
        "revenueMode": "mixed_presale_by_component",
        "defaultCollection": "asset_component_schedule",
        "recoveryHaircut": 0.20,
        "ltvRiskThreshold": 0.80,
    },
    "logistics": {
        "revenueMode": "lease_forward_sale_refinancing",
        "defaultCollection": "forward_sale_closing",
        "recoveryHaircut": 0.15,
        "ltvRiskThreshold": 0.70,
    },
    "lodging": {
        "revenueMode": "serviced_residence_presale_or_operation",
        "defaultCollection": "10-50-40",
        "recoveryHaircut": 0.35,
        "ltvRiskThreshold": 0.75,
    },
    "other": {
        "revenueMode": "manual_review",
        "defaultCollection": "manual",
        "recoveryHaircut": 0.25,
        "ltvRiskThreshold": 0.80,
    },
}


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
        engine_readiness = self._downstream_engine_readiness(project_data, revenue, costs, pf_structure, quality)
        pf["isPfReviewReady"] = pf["status"] == "ready" and not quality["missingFields"]
        pf["requiredMissingFields"] = quality["missingFields"]
        pf["requiresCashFlowScenario"] = engine_readiness["requiresCashFlowScenario"]
        pf["requiresUnsoldLtvMatrix"] = engine_readiness["requiresUnsoldLtvMatrix"]
        pf["requiresWaterfallAnalysis"] = engine_readiness["requiresWaterfallAnalysis"]
        asset_profile_summary = self._asset_profile_summary(project_data)
        logistics_analysis = self._logistics_analysis(project_data, pf_structure)
        feasibility_v2_draft = self._feasibility_study_v2_draft(
            project_data,
            program,
            revenue,
            costs,
            returns,
            pf,
            sensitivity,
            asset_profile_summary,
            logistics_analysis,
        )
        cash_flow_request_draft = build_cash_flow_run_request_from_feasibility_study(feasibility_v2_draft)
        cash_flow_run_result = run_cash_flow_engine(cash_flow_request_draft)

        return {
            "analysisType": "developer_and_pf_feasibility",
            "agentRole": "development_feasibility_orchestrator",
            "summary": self._summary(returns, pf, quality),
            "architecturalProgram": program,
            "revenue": revenue,
            "costs": costs,
            "developerReturns": returns,
            "pfReadiness": pf,
            "sensitivity": sensitivity,
            "downstreamEngineReadiness": engine_readiness,
            "assetCalculationProfiles": asset_profile_summary,
            "logisticsAnalysis": logistics_analysis,
            "feasibilityStudyV2Integration": self._feasibility_study_v2_integration(project_data, feasibility_v2_draft),
            "feasibilityStudyV2Draft": feasibility_v2_draft,
            "cashFlowRunRequestDraft": cash_flow_request_draft,
            "cashFlowRunResult": cash_flow_run_result,
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
            "salesValueLtv": calculate_sales_value_ltv(total_pf, total_revenue),
            "ltvByRevenue": calculate_sales_value_ltv(total_pf, total_revenue),
            "ltvByRevenueBasis": "gross_sales_value",
            "ltcByCost": calculate_ltc(total_pf, total_cost),
            "equityRatio": max(0, total_cost - total_pf) / total_cost if total_cost else 0,
            "landCostRatio": costs["costRatio"]["land"],
            "constructionCostRatio": costs["costRatio"]["construction"],
            "financialCostRatio": costs["costRatio"]["finance"],
            "developerProfitMargin": (total_revenue - total_cost) / total_revenue if total_revenue else 0,
            "exitSaleRate": calculate_exit_sale_rate(total_pf, costs["financeCost"], total_revenue),
            "estimatedExitSalesRate": calculate_exit_sale_rate(total_pf, costs["financeCost"], total_revenue),
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

    def _downstream_engine_readiness(
        self,
        project_data: Dict[str, Any],
        revenue: Dict[str, Any],
        costs: Dict[str, Any],
        pf_structure: List[Dict[str, Any]],
        quality: Dict[str, Any],
    ) -> Dict[str, Any]:
        has_revenue = revenue["totalRevenue"] > 0
        has_cost = costs["totalCost"] > 0
        has_pf = bool(pf_structure)
        has_schedule = bool(
            project_data.get("cashflow_input")
            or project_data.get("schedule")
            or project_data.get("sales_schedule")
            or project_data.get("salesCollectionSchedule")
        )
        has_sales_or_product_mix = bool(
            project_data.get("product_mix")
            or project_data.get("productMix")
            or project_data.get("sales_assumptions")
            or revenue["salesRevenue"] > 0
        )
        blockers = []
        if not has_revenue:
            blockers.append("total_revenue")
        if not has_cost:
            blockers.append("total_cost")
        if not has_schedule:
            blockers.append("schedule_or_cashflow_input")
        if not has_pf:
            blockers.append("pf_structure")

        return {
            "role": "delegate_to_specialized_engines",
            "canonicalPath": [
                "DevelopmentFeasibilityAgent",
                "FeasibilityStudyV2",
                "CashFlowRunRequest",
                "CashFlowEngine",
                "CalculationScenarioEngine",
            ],
            "canRunCashFlow": has_revenue and has_cost and has_schedule,
            "canRunPfScenarios": has_pf and has_revenue,
            "canRunUnsoldLtvMatrix": has_pf and has_revenue and has_sales_or_product_mix,
            "requiresCashFlowScenario": has_revenue and has_cost,
            "requiresWaterfallAnalysis": has_pf,
            "requiresUnsoldLtvMatrix": has_pf and has_sales_or_product_mix,
            "delegationPolicy": (
                "DevelopmentFeasibilityAgent assesses feasibility and readiness; "
                "monthly cash flow, waterfall, unsold LTV, and tranche recovery are delegated "
                "to Cash Flow Engine and Calculation Scenario Engine."
            ),
            "blockingFields": list(dict.fromkeys(blockers + quality["missingFields"])),
        }

    def _feasibility_study_v2_integration(
        self,
        project_data: Dict[str, Any],
        draft: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "canonicalSchema": FEASIBILITY_STUDY_SCHEMA_VERSION,
            "workbookContract": FEASIBILITY_WORKBOOK_VERSION,
            "sourceSchemaPath": "services/api-server/schemas/feasibility_study.py",
            "mapperStatus": "cash_flow_request_draft_available",
            "requiredMappers": [
                "DevelopmentFeasibilityAgent output -> FeasibilityStudyV2 draft",
                "FeasibilityStudyV2 -> CashFlowRunRequest",
            ],
            "draftFieldCounts": {
                "revenueItems": len(draft.get("revenue_items", [])),
                "costItems": len(draft.get("cost_items", [])),
                "financing": len(draft.get("financing", [])),
                "schedule": len(draft.get("schedule", [])),
                "scenarios": len(draft.get("scenarios", [])),
            },
            "inputAlreadyCanonical": project_data.get("version") == "realytics-feasibility-study-v2",
        }

    def _feasibility_study_v2_draft(
        self,
        project_data: Dict[str, Any],
        program: Dict[str, Any],
        revenue: Dict[str, Any],
        costs: Dict[str, Any],
        returns: Dict[str, Any],
        pf: Dict[str, Any],
        sensitivity: List[Dict[str, Any]],
        asset_profile_summary: Dict[str, Any],
        logistics_analysis: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        project = project_data.get("project", {})
        land = project_data.get("land", {})
        regulation = project_data.get("regulation", {})
        schedule = project_data.get("schedule", {})
        canonical_study = project_data.get("feasibilityStudyV2") or project_data.get("canonicalFeasibilityStudyV2") or {}
        canonical_raw_import = canonical_study.get("raw_import") if isinstance(canonical_study, dict) else {}
        raw_import = dict(canonical_raw_import) if isinstance(canonical_raw_import, dict) else {}
        raw_import.update({
            "source": "development_feasibility_agent",
            "sourceAnalysisType": "developer_and_pf_feasibility",
            "note": "Draft mapping for FeasibilityStudyV2; workbook cell provenance is added by the workbook adapter.",
            "assetCalculationProfiles": asset_profile_summary,
            "logisticsAnalysis": logistics_analysis or {},
        })
        return {
            "version": FEASIBILITY_STUDY_SCHEMA_VERSION,
            "project_id": project.get("id"),
            "project_name": project.get("name"),
            "asset_type": self._asset_type(project.get("type")),
            "currency": "KRW",
            "site": {
                "address": land.get("address"),
                "land_area_sqm": self._value("land_area_sqm", "Land area", program["landAreaSqm"], "SQM"),
                "zoning": regulation.get("zoning"),
                "legal_far": self._value("legal_far", "Legal FAR", regulation.get("legal_far"), "PERCENT"),
                "legal_bcr": self._value("legal_bcr", "Legal BCR", regulation.get("legal_bcr"), "PERCENT"),
            },
            "program": {
                "gross_floor_area_sqm": self._value("gross_floor_area_sqm", "Gross floor area", program["grossFloorAreaSqm"], "SQM"),
                "saleable_area_sqm": self._value("saleable_area_sqm", "Saleable area", program["saleableAreaSqm"], "SQM"),
                "unit_count": self._value(
                    "unit_count",
                    "Unit count",
                    project_data.get("architectural_plan", {}).get("unit_count"),
                    "COUNT",
                ),
                "parking_count": self._value(
                    "parking_count",
                    "Parking count",
                    project_data.get("architectural_plan", {}).get("parking_count"),
                    "COUNT",
                ),
            },
            "product_mix": self._product_mix_v2(project_data, revenue),
            "revenue_items": [
                self._line_item("sales_revenue", "Sales revenue", "sales", revenue["salesRevenue"]),
                self._line_item("other_revenue", "Other revenue", "other_revenue", revenue["otherRevenue"]),
            ],
            "cost_items": [
                self._line_item("land_cost", "Land cost", "land", costs["landCost"]),
                self._line_item("construction_cost", "Construction cost", "construction", costs["constructionCost"]),
                self._line_item("finance_cost", "Finance cost", "finance", costs["financeCost"]),
                self._line_item("other_cost", "Other cost", "other", costs["otherCost"]),
            ],
            "financing": self._financing_v2(project_data.get("pf_structure", [])),
            "schedule": self._schedule_v2(schedule),
            "scenarios": self._scenarios_v2(sensitivity),
            "metrics": {
                "total_revenue": revenue["totalRevenue"],
                "total_cost": costs["totalCost"],
                "development_profit": returns["developmentProfit"],
                "profit_margin": returns["profitMargin"],
                "profit_on_cost": returns["profitOnCost"],
                "break_even_sale_rate": returns["breakEvenSaleRate"],
                "required_equity": pf["requiredEquity"],
                "ltv": pf["ltvByRevenue"],
                "ltc": pf["ltcByCost"],
                "exit_sale_rate": pf["exitSaleRate"],
            },
            "raw_import": raw_import,
        }

    def _logistics_analysis(
        self,
        project_data: Dict[str, Any],
        pf_structure: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        project = project_data.get("project", {})
        if self._asset_type(project.get("type")) != "logistics":
            return {
                "applicable": False,
                "reason": "Project is not classified as a logistics asset.",
            }

        pf_amount = sum(float(item.get("amount") or 0) for item in pf_structure)
        leasable_area_py = float(project.get("leasable_area_py") or project_data.get("logistics", {}).get("leasable_area_py") or 0)
        rent_per_py = float(project.get("rent_per_py") or project_data.get("logistics", {}).get("rent_per_py") or 0)
        operating_expenses = float(project.get("operating_expenses") or project_data.get("logistics", {}).get("operating_expenses") or 0)
        cap_rate = float(project.get("cap_rate") or project_data.get("logistics", {}).get("cap_rate") or 0)
        noi = float(project.get("noi") or calculate_logistics_noi(leasable_area_py, rent_per_py, operating_expenses))
        exit_value = calculate_exit_value(noi, cap_rate)
        forward_sale_price = float(project.get("forward_sale_price") or project_data.get("logistics", {}).get("forward_sale_price") or 0)
        appraisal_value = float(project.get("appraisal_value") or project_data.get("logistics", {}).get("appraisal_value") or 0)
        tenant_score = calculate_tenant_stability_score(
            str(project.get("tenant_credit_rating") or ""),
            int(project.get("lease_term_months") or 0),
        )
        location_score = calculate_location_score(
            float(project.get("distance_to_ic_km") or 999),
            float(project.get("distance_to_port_km") or 999),
        )
        spec_score = calculate_spec_adequacy_score(
            float(project.get("clear_height") or 0),
            float(project.get("floor_load") or 0),
            bool(project.get("ramp_available")),
        )
        score = round((tenant_score * 0.4) + (location_score * 0.3) + (spec_score * 0.3), 2)
        forward_sale_ltv = calculate_forward_sale_ltv(pf_amount, forward_sale_price)
        appraisal_ltv = calculate_appraisal_ltv(pf_amount, appraisal_value) if appraisal_value else None
        risk_flags = []
        if forward_sale_price <= 0:
            risk_flags.append("FORWARD_SALE_PRICE_MISSING")
        elif forward_sale_ltv > 0.80:
            risk_flags.append("FORWARD_SALE_LTV_HIGH")
        if cap_rate <= 0:
            risk_flags.append("CAP_RATE_MISSING")
        if tenant_score < 70:
            risk_flags.append("TENANT_STABILITY_WEAK")
        if spec_score < 70:
            risk_flags.append("LOGISTICS_SPEC_WEAK")

        return {
            "applicable": True,
            "schemaVersion": "realytics-logistics-analysis-v1",
            "revenueMode": "lease_forward_sale_refinancing",
            "tenant": {
                "name": project.get("tenant_name"),
                "creditRating": project.get("tenant_credit_rating"),
                "leaseTermMonths": project.get("lease_term_months"),
                "stabilityScore": tenant_score,
            },
            "lease": {
                "leasableAreaPy": leasable_area_py,
                "rentPerPy": rent_per_py,
                "annualNoi": noi,
                "operatingExpenses": operating_expenses,
            },
            "exit": {
                "capRate": cap_rate,
                "capRateExitValue": exit_value,
                "forwardSalePrice": forward_sale_price,
                "forwardBuyer": project.get("forward_buyer"),
                "forwardSaleLtv": forward_sale_ltv,
                "appraisalValue": appraisal_value or None,
                "appraisalLtv": appraisal_ltv,
            },
            "location": {
                "distanceToIcKm": project.get("distance_to_ic_km"),
                "distanceToPortKm": project.get("distance_to_port_km"),
                "score": location_score,
            },
            "spec": {
                "clearHeight": project.get("clear_height"),
                "floorLoad": project.get("floor_load"),
                "dockCount": project.get("dock_count"),
                "rampAvailable": bool(project.get("ramp_available")),
                "coldChainSpec": bool(project.get("cold_chain_spec")),
                "score": spec_score,
            },
            "score": score,
            "riskFlags": risk_flags,
            "riskItems": assess_logistics_risks(project),
            "formulas": {
                "NOI": "leasable_area_py * rent_per_py * 12 - operating_expenses",
                "CapRateExitValue": "NOI / cap_rate",
                "ForwardSaleLTV": "pf_amount / forward_sale_price",
                "AppraisalLTV": "pf_amount / appraisal_value",
            },
        }

    def _product_mix_v2(self, project_data: Dict[str, Any], revenue: Dict[str, Any]) -> List[Dict[str, Any]]:
        product_mix = project_data.get("product_mix") or project_data.get("productMix") or []
        if not product_mix:
            return []
        rows = []
        for index, item in enumerate(product_mix, start=1):
            contract_area_py = item.get("contractAreaPy") or item.get("contract_area_py")
            rows.append({
                "key": item.get("key") or item.get("type") or f"product-{index}",
                "asset_type": self._asset_type(item.get("productType") or item.get("asset_type") or item.get("type")),
                "product_name": item.get("product_name") or item.get("type") or f"Product {index}",
                "unit_count": item.get("units") or item.get("unit_count"),
                "exclusive_area_sqm": self._py_to_sqm_or_none(item.get("exclusiveAreaPy") or item.get("exclusive_area_py")),
                "contract_area_sqm": self._py_to_sqm_or_none(contract_area_py),
                "avg_sale_price_per_py": item.get("pricePerPy") or item.get("price_per_py") or item.get("pricePerContractPy"),
                "revenue": item.get("totalRevenue") or item.get("total_revenue"),
                "excel_row_id": item.get("excel_row_id"),
            })
        return rows

    def _asset_profile_summary(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        product_mix = project_data.get("product_mix") or project_data.get("productMix") or []
        if product_mix:
            asset_types = [
                self._asset_type(item.get("productType") or item.get("asset_type") or item.get("type"))
                for item in product_mix
            ]
        else:
            asset_types = [self._asset_type(project_data.get("project", {}).get("type"))]
        asset_types = list(dict.fromkeys(asset_types))
        return {
            "profileVersion": "asset-calculation-profile-v1",
            "assetTypes": asset_types,
            "profiles": {
                asset_type: ASSET_CALCULATION_PROFILES.get(asset_type, ASSET_CALCULATION_PROFILES["other"])
                for asset_type in asset_types
            },
            "mappingRule": (
                "FeasibilityStudyV2 -> CashFlowRunRequest must apply revenue mode, default collection schedule, "
                "recovery haircut, and LTV risk threshold by asset type."
            ),
        }

    def _financing_v2(self, pf_structure: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows = []
        for index, tranche in enumerate(pf_structure, start=1):
            rows.append({
                "tranche_id": tranche.get("tranche_id") or tranche.get("tranche") or tranche.get("name") or f"tranche-{index}",
                "name": tranche.get("name") or tranche.get("tranche") or f"Tranche {index}",
                "priority": self._priority_label(tranche, index),
                "amount": tranche.get("amount"),
                "interest_rate": tranche.get("interest_rate") or tranche.get("interestRate"),
                "fee_rate": tranche.get("fee_rate") or tranche.get("feeRate"),
                "term_months": tranche.get("term_months") or tranche.get("maturityMonths"),
                "repayment_source": tranche.get("repayment_source") or "sales_proceeds",
            })
        return rows

    def _schedule_v2(self, schedule: Any) -> List[Dict[str, Any]]:
        labels = {
            "land_purchase": "Land purchase",
            "permit_approval": "Permit approval",
            "construction_start": "Construction start",
            "presale_start": "Presale start",
            "completion": "Completion",
            "loan_maturity": "Loan maturity",
        }
        # Legacy shape: a dict of {key: date}.
        if isinstance(schedule, dict):
            return [
                {"key": key, "label": labels.get(key, key), "planned_date": self._schedule_date_value(value)}
                for key, value in schedule.items()
                if value
            ]
        # Canonical FeasibilityStudyV2 shape: a list of {key, label, planned_date, ...}.
        if isinstance(schedule, list):
            rows: List[Dict[str, Any]] = []
            for item in schedule:
                if not isinstance(item, dict):
                    continue
                key = item.get("key") or item.get("name")
                planned = item.get("planned_date") or item.get("plannedDate")
                if planned is None and item.get("value") is not None:
                    planned = self._schedule_date_value(item.get("value"))
                if key and planned:
                    rows.append({"key": key, "label": item.get("label") or labels.get(key, key), "planned_date": planned})
            return rows
        return []

    @staticmethod
    def _schedule_date_value(value: Any) -> str:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"M{int(value)}"
        return str(value)

    def _scenarios_v2(self, sensitivity: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows = []
        for row in sensitivity:
            scenario = row.get("scenario", "scenario")
            rows.append({
                "scenario_id": scenario,
                "label": scenario.replace("_", " ").title(),
                "sales_price_factor": row.get("totalRevenue", 0) / sensitivity[0].get("totalRevenue", 1) if sensitivity and sensitivity[0].get("totalRevenue") else 1.0,
                "construction_cost_factor": 1.0,
                "interest_rate_addition": 0.0,
            })
        return rows

    def _line_item(self, key: str, label: str, category: str, amount: Any) -> Dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "category": category,
            "amount": amount or 0,
            "unit": "KRW",
        }

    def _value(self, key: str, label: str, value: Any, unit: str) -> Dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "value": value or 0,
            "unit": unit,
        }

    def _asset_type(self, raw_type: Any) -> str:
        value = str(raw_type or "").lower()
        if value in {"mixed_use", "mixed_use_residential"}:
            return "mixed_use"
        if "officetel" in value:
            return "officetel"
        if "apartment" in value or value == "residential":
            return "apartment"
        if "knowledge" in value:
            return "knowledge_industry_center"
        if "commercial" in value or "retail" in value:
            return "commercial"
        if "logistics" in value:
            return "logistics"
        if "lodging" in value or "serviced" in value:
            return "lodging"
        return "other"

    def _priority_label(self, tranche: Dict[str, Any], index: int) -> str:
        value = str(tranche.get("priority") or tranche.get("name") or tranche.get("tranche") or "").lower()
        tranche_name = str(tranche.get("name") or tranche.get("tranche") or "").lower()
        if value in {"1", "senior"} or "senior" in tranche_name or "tr.a" in tranche_name or "tranche a" in tranche_name:
            return "senior"
        if value in {"2", "middle", "mezzanine"} or "mezz" in tranche_name or "middle" in tranche_name or "tr.b" in tranche_name or "tranche b" in tranche_name:
            return "middle"
        if value in {"3", "junior"} or "junior" in tranche_name or "tr.c" in tranche_name or "tranche c" in tranche_name:
            return "junior"
        return "other" if index > 3 else ["senior", "middle", "junior"][index - 1]

    def _py_to_sqm_or_none(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        return float(value) / 0.3025

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
