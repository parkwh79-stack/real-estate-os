import math
from typing import Any, Dict, List

from agents.cash_flow_engine import run_cash_flow_engine
from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study
from core.financial_input_validation import FinancialInputError, require_finite_numbers
from core.schedule import schedule_month_indices
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
from schemas.feasibility_study import FEASIBILITY_STUDY_SCHEMA_VERSION, FEASIBILITY_WORKBOOK_VERSION, ScheduleMilestone


DEVELOPMENT_FEASIBILITY_AGENT_VERSION = "2.1.0"

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
        require_finite_numbers(project_data, "developmentFeasibility")
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
        cash_flow_request_draft = (
            None
            if pf["missingFields"]
            else build_cash_flow_run_request_from_feasibility_study(feasibility_v2_draft)
        )
        cash_flow_run_result = None
        cash_flow_issue = None
        try:
            if pf["missingFields"]:
                raise FinancialInputError(
                    "대출 금액이 미입력된 금융 조건을 확인해 주세요.",
                    code="financing_amount_missing",
                )
            cash_flow_run_result = run_cash_flow_engine(cash_flow_request_draft)
        except FinancialInputError as exc:
            if exc.code not in {"cash_flow_cost_total_mismatch", "financing_amount_missing"}:
                raise
            if exc.code == "financing_amount_missing":
                cash_flow_issue = {
                    "code": exc.code,
                    "severity": "blocking",
                    "message": "대출 금액이 미입력되어 현금흐름 계산을 보류했습니다.",
                    "requiredEvidence": "대출 조건별 금액 및 산출 근거",
                    "sourcePaths": pf["missingFields"],
                }
            else:
                cash_flow_issue = {
                    "code": exc.code,
                    "severity": "blocking",
                    "message": "총사업비와 월별 비용 합계가 일치하지 않아 현금흐름 계산을 보류했습니다.",
                    "requiredEvidence": "항목별·월별 비용 배분 근거 및 총사업비 대사",
                    "sourcePaths": ["sourcesAndUses.cost.totalProjectCost", "costSchedule.rows"],
                }
                missing_field = "cash_flow.monthly_cost_allocation"
                quality["missingFields"].append(missing_field)
                engine_readiness["blockingFields"].append(missing_field)
            quality["warnings"].append(cash_flow_issue["message"])
            quality["blockingIssues"] = [cash_flow_issue]
            quality["status"] = "needs_review"
            quality["confidence"] = max(0.2, 1 - len(quality["missingFields"]) * 0.15)
            pf["isPfReviewReady"] = False
            for key in ("canRunCashFlow", "canRunPfScenarios", "canRunUnsoldLtvMatrix"):
                engine_readiness[key] = False

        return {
            **({"status": "completed_with_review", "dataStatus": "partial"} if cash_flow_issue else {}),
            "analysisType": "developer_and_pf_feasibility",
            "engineVersion": DEVELOPMENT_FEASIBILITY_AGENT_VERSION,
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
        missing_fields = self._missing_pf_amount_fields(pf_structure)
        financing = self._financing_v2(pf_structure)
        debt = [row for row in financing if row["priority"] != "equity"]
        equity = [row for row in financing if row["priority"] == "equity"]
        bridge = [row for row in debt if row["priority"] == "bridge"]
        pf_loans = [row for row in debt if row["priority"] != "bridge"]
        def known_total(rows: List[Dict[str, Any]]) -> float | None:
            return None if any(row.get("amount") in (None, "") for row in rows) else sum(row.get("amount", 0) for row in rows)
        total_pf = known_total(debt)
        total_revenue = revenue["totalRevenue"]
        total_cost = costs["totalCost"]
        recommended_by_revenue = total_revenue * 0.65
        recommended_by_cost = total_cost * 0.75
        recommended_pf_limit = min(recommended_by_revenue, recommended_by_cost) if total_revenue and total_cost else 0
        return {
            "totalPfAmount": total_pf,
            "pfLoanAmount": known_total(pf_loans),
            "bridgeLoanAmount": known_total(bridge),
            "committedEquityAmount": known_total(equity),
            "totalPfAmountBasis": "debt_including_bridge_excluding_equity",
            "salesValueLtv": calculate_sales_value_ltv(total_pf, total_revenue) if total_pf is not None else None,
            "ltvByRevenue": calculate_sales_value_ltv(total_pf, total_revenue) if total_pf is not None else None,
            "ltvByRevenueBasis": "gross_sales_value",
            "ltcByCost": calculate_ltc(total_pf, total_cost) if total_pf is not None else None,
            "equityRatio": (max(0, total_cost - total_pf) / total_cost if total_cost else 0) if total_pf is not None else None,
            "landCostRatio": costs["costRatio"]["land"],
            "constructionCostRatio": costs["costRatio"]["construction"],
            "financialCostRatio": costs["costRatio"]["finance"],
            "developerProfitMargin": (total_revenue - total_cost) / total_revenue if total_revenue else 0,
            "exitSaleRate": calculate_exit_sale_rate(total_pf, costs["financeCost"], total_revenue) if total_pf is not None else None,
            "estimatedExitSalesRate": calculate_exit_sale_rate(total_pf, costs["financeCost"], total_revenue) if total_pf is not None else None,
            "requiredEquity": max(0, total_cost - total_pf) if total_pf is not None else None,
            "recommendedPfLimit": recommended_pf_limit,
            "headroomToRecommendedLimit": recommended_pf_limit - total_pf if total_pf is not None else None,
            "status": "needs_input" if missing_fields else "ready" if total_pf <= recommended_pf_limit or not recommended_pf_limit else "leverage_review_required",
            "missingFields": missing_fields,
        }

    @staticmethod
    def _missing_pf_amount_fields(pf_structure: List[Dict[str, Any]]) -> List[str]:
        return [
            f"pf_structure.{tranche.get('tranche_id') or tranche.get('tranche') or tranche.get('name') or index}.amount"
            for index, tranche in enumerate(pf_structure)
            if tranche.get("amount") in (None, "")
        ]

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
        missing.extend(self._missing_pf_amount_fields(project_data.get("pf_structure", [])))
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
        missing_pf_amounts = self._missing_pf_amount_fields(pf_structure)
        has_pf = any(row["priority"] != "equity" for row in self._financing_v2(pf_structure)) and not missing_pf_amounts
        has_schedule = bool(
            project_data.get("cashflow_input")
            or self._schedule_v2(project_data.get("schedule"))
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
            "canRunCashFlow": has_revenue and has_cost and has_schedule and not missing_pf_amounts,
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
            "mapperStatus": (
                "financing_amount_required"
                if self._missing_pf_amount_fields(draft.get("financing", []))
                else "cash_flow_request_draft_available"
            ),
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

        logistics = project_data.get("logistics") or {}
        input_sources = {}
        input_issues = []

        def raw(key):
            # An explicit zero/false/null in project must not revive an older
            # alternate value. Only an absent key falls back to logistics.
            source = "project" if key in project else "logistics"
            input_sources[key] = f"{source}.{key}"
            return project.get(key) if source == "project" else logistics.get(key)

        def number(key, *, signed=False, positive=False, integer=False):
            value = raw(key)
            if value is None or value == "":
                input_issues.append({"field": key, "reason": "missing"})
                return None
            try:
                result = float(value)
            except (TypeError, ValueError):
                result = float("nan")
            if (isinstance(value, bool) or not math.isfinite(result)
                    or (not signed and result < 0) or (positive and result <= 0)
                    or (integer and not result.is_integer())):
                input_issues.append({"field": key, "reason": "invalid"})
                return None
            return int(result) if integer else result

        def boolean(key):
            value = raw(key)
            if value is None:
                return None
            if not isinstance(value, bool):
                input_issues.append({"field": key, "reason": "invalid"})
                return None
            return value

        financing = self._financing_v2(pf_structure)
        debt_amounts = [item.get("amount") for item in financing if item["priority"] != "equity"]
        pf_amount = sum(float(value) for value in debt_amounts) if financing and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
            for value in debt_amounts
        ) else None
        if pf_amount is None:
            input_issues.append({"field": "pf_structure.amount", "reason": "missing_or_invalid"})
        leasable_area_py = number("leasable_area_py")
        rent_per_py = number("rent_per_py")
        operating_expenses = number("operating_expenses")
        cap_rate = number("cap_rate", positive=True)
        supplied_noi = raw("noi")
        noi = number("noi", signed=True) if supplied_noi is not None and supplied_noi != "" else None
        noi_basis = "supplied_analysis_input" if noi is not None else "unavailable"
        # Invalid supplied NOI is not silently replaced by a calculation.
        if supplied_noi in (None, "") and all(value is not None for value in (leasable_area_py, rent_per_py, operating_expenses)):
            noi = calculate_logistics_noi(leasable_area_py, rent_per_py, operating_expenses)
            noi_basis = "calculated_from_lease_inputs"
        exit_value = calculate_exit_value(noi, cap_rate) if noi is not None and cap_rate is not None else None
        forward_sale_price = number("forward_sale_price", positive=True)
        appraisal_value = number("appraisal_value", positive=True)
        rating = raw("tenant_credit_rating")
        rating = rating.strip() if isinstance(rating, str) and rating.strip() else None
        if rating is None:
            input_issues.append({"field": "tenant_credit_rating", "reason": "missing"})
        lease_term = number("lease_term_months", positive=True, integer=True)
        distance_ic = number("distance_to_ic_km")
        distance_port = number("distance_to_port_km")
        clear_height = number("clear_height", positive=True)
        floor_load = number("floor_load", positive=True)
        ramp = boolean("ramp_available")
        if ramp is None and not any(issue["field"] == "ramp_available" for issue in input_issues):
            input_issues.append({"field": "ramp_available", "reason": "missing"})
        tenant_score = calculate_tenant_stability_score(rating, lease_term) if rating is not None and lease_term is not None else None
        location_score = calculate_location_score(distance_ic, distance_port) if distance_ic is not None and distance_port is not None else None
        spec_score = calculate_spec_adequacy_score(clear_height, floor_load, ramp) if all(value is not None for value in (clear_height, floor_load, ramp)) else None
        score = round(tenant_score * 0.4 + location_score * 0.3 + spec_score * 0.3, 2) if all(
            value is not None for value in (tenant_score, location_score, spec_score)
        ) else None
        forward_sale_ltv = calculate_forward_sale_ltv(pf_amount, forward_sale_price) if pf_amount is not None and forward_sale_price is not None else None
        appraisal_ltv = calculate_appraisal_ltv(pf_amount, appraisal_value) if pf_amount is not None and appraisal_value is not None else None
        risk_flags = []
        if forward_sale_price is None:
            risk_flags.append("FORWARD_SALE_PRICE_MISSING")
        elif forward_sale_ltv is not None and forward_sale_ltv > 0.80:
            risk_flags.append("FORWARD_SALE_LTV_HIGH")
        if cap_rate is None:
            risk_flags.append("CAP_RATE_MISSING")
        if tenant_score is not None and tenant_score < 70:
            risk_flags.append("TENANT_STABILITY_WEAK")
        if spec_score is not None and spec_score < 70:
            risk_flags.append("LOGISTICS_SPEC_WEAK")
        metrics = {"annualNoi": noi, "capRateExitValue": exit_value, "forwardSaleLtv": forward_sale_ltv,
                   "appraisalLtv": appraisal_ltv, "tenantScore": tenant_score, "locationScore": location_score,
                   "specScore": spec_score, "score": score}
        return {
            "applicable": True,
            "schemaVersion": "realytics-logistics-analysis-v1",
            "calculationPolicy": "logistics-available-inputs-v2",
            "status": "partial" if any(value is None for value in metrics.values()) else "calculated",
            "unavailableMetrics": [key for key, value in metrics.items() if value is None],
            "inputIssues": input_issues,
            "inputSources": input_sources,
            "evidenceScope": "analysis_inputs_not_document_verification",
            "scoreBasis": "existing_rule_based_indicator_not_credit_approval",
            "revenueMode": "lease_forward_sale_refinancing",
            "tenant": {"name": raw("tenant_name"), "creditRating": rating,
                       "leaseTermMonths": lease_term, "stabilityScore": tenant_score},
            "lease": {"leasableAreaPy": leasable_area_py, "rentPerPy": rent_per_py,
                      "annualNoi": noi, "noiBasis": noi_basis, "operatingExpenses": operating_expenses},
            "exit": {"capRate": cap_rate, "capRateExitValue": exit_value,
                     "forwardSalePrice": forward_sale_price, "forwardBuyer": raw("forward_buyer"),
                     "pfAmount": pf_amount, "forwardSaleLtv": forward_sale_ltv,
                     "appraisalValue": appraisal_value, "appraisalLtv": appraisal_ltv},
            "location": {"distanceToIcKm": distance_ic, "distanceToPortKm": distance_port, "score": location_score},
            "spec": {"clearHeight": clear_height, "floorLoad": floor_load,
                     "dockCount": raw("dock_count"), "rampAvailable": ramp,
                     "coldChainSpec": boolean("cold_chain_spec"), "score": spec_score},
            "score": score,
            "riskFlags": risk_flags,
            "riskItems": assess_logistics_risks({"tenant_credit_rating": rating, "pre_sale_contract_status": raw("pre_sale_contract_status")}),
            "formulas": {"NOI": "leasable_area_py * rent_per_py * 12 - operating_expenses",
                         "CapRateExitValue": "NOI / cap_rate", "ForwardSaleLTV": "pf_amount / forward_sale_price",
                         "AppraisalLTV": "pf_amount / appraisal_value"},
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
                "interest_rate": (
                    tranche.get("interest_rate")
                    if tranche.get("interest_rate") is not None
                    else tranche.get("interestRate")
                ),
                "fee_rate": (
                    tranche.get("fee_rate")
                    if tranche.get("fee_rate") is not None
                    else tranche.get("feeRate")
                ),
                "term_months": (
                    tranche.get("term_months")
                    if tranche.get("term_months") is not None
                    else tranche.get("maturityMonths")
                ),
                "repayment_source": tranche.get("repayment_source"),
            })
        return rows

    def _schedule_v2(self, schedule: Any) -> List[Dict[str, Any]]:
        month_indices = schedule_month_indices(schedule)
        labels = {
            "land_purchase": "Land purchase",
            "permit_approval": "Permit approval",
            "construction_start": "Construction start",
            "presale_start": "Presale start",
            "completion": "Completion",
            "loan_maturity": "Loan maturity",
        }
        if isinstance(schedule, dict):
            entries = [(str(key), value) for key, value in schedule.items()]
        elif isinstance(schedule, list):
            entries = [
                (next((item.get(field) for field in ("key", "type", "event", "eventKey", "event_key", "name") if item.get(field)), None), item)
                for item in schedule if isinstance(item, dict)
            ]
        else:
            return []
        rows: List[Dict[str, Any]] = []
        for key, value in entries:
            item = value if isinstance(value, dict) else {}
            planned = self._schedule_date_value(value)
            explicit_month = next((item[alias] for alias in ("month_index", "monthIndex") if alias in item), None)
            if not key or (not planned and explicit_month is None):
                continue
            row = {"key": key, "label": item.get("label") or labels.get(key, key), "planned_date": planned}
            for field in ("actual_date", "excel", "provenance"):
                if field in item:
                    row[field] = item[field]
            if explicit_month is not None:
                row["month_index"] = month_indices[key]
            try:
                ScheduleMilestone.model_validate(row)
            except ValueError as exc:
                raise FinancialInputError(
                    f"{key}: 일정 및 원본 출처 형식을 확인해 주세요.", code="invalid_schedule",
                ) from exc
            rows.append(row)
        return rows

    @staticmethod
    def _schedule_date_value(value: Any) -> str | None:
        if isinstance(value, dict):
            # A cleared canonical date must not resurrect a stale legacy alias.
            for key in ("planned_date", "plannedDate", "value"):
                if key in value:
                    return DevelopmentFeasibilityAgent._schedule_date_value(value[key])
            return None
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"M{int(value)}"
        return value if isinstance(value, str) else None

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
        explicit_priority = str(tranche.get("priority") or "").lower()
        if explicit_priority in {"senior", "mezzanine", "junior", "equity", "bridge", "other"}:
            return explicit_priority
        value = str(tranche.get("priority") or tranche.get("name") or tranche.get("tranche") or "").lower()
        tranche_name = str(tranche.get("name") or tranche.get("tranche") or "").lower()
        if value in {"1", "senior"} or "senior" in tranche_name or "tr.a" in tranche_name or "tranche a" in tranche_name:
            return "senior"
        if value in {"2", "middle", "mezzanine"} or "mezz" in tranche_name or "middle" in tranche_name or "tr.b" in tranche_name or "tranche b" in tranche_name:
            return "mezzanine"
        if value in {"3", "junior"} or "junior" in tranche_name or "tr.c" in tranche_name or "tranche c" in tranche_name:
            return "junior"
        return "other" if index > 3 else ["senior", "mezzanine", "junior"][index - 1]

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
