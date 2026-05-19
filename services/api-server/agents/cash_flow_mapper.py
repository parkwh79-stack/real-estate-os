from __future__ import annotations

from typing import Any, Dict, List

from schemas.feasibility_study import FeasibilityStudyV2


def build_cash_flow_run_request_from_feasibility_study(study_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build an asset-aware CashFlowRunRequest draft from FeasibilityStudyV2 data."""
    study = FeasibilityStudyV2.model_validate(study_data)
    raw_import = dict(study.raw_import or {})
    asset_profiles = raw_import.get("assetCalculationProfiles") or _default_asset_profile_summary(study.asset_type.value)
    product_mix = _product_mix(study, asset_profiles)
    revenue = _revenue_sources(study)
    costs = _cost_uses(study)
    funding = _funding_sources(study)
    diagnostics = _mapping_diagnostics(study, product_mix, revenue, costs, funding)

    return {
        "requestType": "cash_flow_run_request_v1",
        "sourceSchema": study.version,
        "project": {
            "projectId": study.project_id,
            "projectName": study.project_name,
            "buildingUse": study.asset_type.value,
            "location": study.site.address,
            "landAreaSqm": _value(study.site.land_area_sqm),
            "grossFloorAreaSqm": _value(study.program.gross_floor_area_sqm),
        },
        "sourcesAndUses": {
            "revenue": revenue,
            "cost": costs,
            "funding": funding,
        },
        "productMix": product_mix,
        "pfStructure": _pf_structure(study),
        "schedule": _schedule(study),
        "scenarioSet": _scenario_set(study),
        "assetCalculationProfiles": asset_profiles,
        "mappingDiagnostics": diagnostics,
        "sourceMetadata": {
            "sourceName": "FeasibilityStudyV2",
            "sourceType": "internal_schema",
            "sourceUrlOrEvidenceId": study.project_id,
            "baseDate": None,
            "confidence": 0.75 if diagnostics["status"] == "mapped_with_warnings" else 0.9,
            "limitations": diagnostics["warnings"],
        },
    }


def _product_mix(study: FeasibilityStudyV2, asset_profiles: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not study.product_mix:
        asset_type = study.asset_type.value
        profile = _profile(asset_profiles, asset_type)
        return [{
            "productId": "aggregate-project",
            "type": asset_type,
            "units": _value(study.program.unit_count),
            "contractAreaSqm": _value(study.program.contract_area_sqm),
            "saleableAreaSqm": _value(study.program.saleable_area_sqm),
            "totalSalesRevenue": study.metrics.total_revenue if study.metrics else 0,
            **profile,
        }]

    rows = []
    for row in study.product_mix:
        asset_type = row.asset_type.value
        profile = _profile(asset_profiles, asset_type)
        rows.append({
            "productId": row.key,
            "type": asset_type,
            "productName": row.product_name,
            "units": row.unit_count,
            "exclusiveAreaSqm": row.exclusive_area_sqm,
            "saleableAreaSqm": row.saleable_area_sqm,
            "contractAreaSqm": row.contract_area_sqm,
            "pricePerSqm": row.avg_sale_price_per_sqm,
            "pricePerPy": row.avg_sale_price_per_py,
            "totalSalesRevenue": row.revenue,
            **profile,
        })
    return rows


def _profile(asset_profiles: Dict[str, Any], asset_type: str) -> Dict[str, Any]:
    profile = (asset_profiles.get("profiles") or {}).get(asset_type) or (asset_profiles.get("profiles") or {}).get("other") or {}
    return {
        "revenueMode": profile.get("revenueMode", "manual_review"),
        "defaultCollection": profile.get("defaultCollection", "manual"),
        "recoveryHaircut": profile.get("recoveryHaircut", 0.25),
        "ltvRiskThreshold": profile.get("ltvRiskThreshold", 0.80),
    }


def _revenue_sources(study: FeasibilityStudyV2) -> Dict[str, Any]:
    result = {
        "totalSalesRevenue": study.metrics.total_revenue if study.metrics else 0,
        "leaseDeposit": 0,
        "forwardSaleProceeds": 0,
        "refinancingProceeds": 0,
        "otherRevenue": 0,
    }
    for item in study.revenue_items:
        if item.category in {"sales", "sales_revenue"}:
            result["totalSalesRevenue"] = item.amount or result["totalSalesRevenue"]
        elif item.category in {"lease", "lease_deposit"}:
            result["leaseDeposit"] += item.amount or 0
        elif item.category in {"forward_sale", "sale_proceeds"}:
            result["forwardSaleProceeds"] += item.amount or 0
        elif item.category in {"refinancing", "refinancing_loan"}:
            result["refinancingProceeds"] += item.amount or 0
        else:
            result["otherRevenue"] += item.amount or 0
    return result


def _cost_uses(study: FeasibilityStudyV2) -> Dict[str, Any]:
    result = {
        "landCost": 0,
        "directConstructionCost": 0,
        "financialCost": 0,
        "otherCost": 0,
        "totalProjectCost": study.metrics.total_cost if study.metrics else 0,
    }
    for item in study.cost_items:
        amount = item.amount or 0
        if item.category == "land":
            result["landCost"] += amount
        elif item.category == "construction":
            result["directConstructionCost"] += amount
        elif item.category == "finance":
            result["financialCost"] += amount
        else:
            result["otherCost"] += amount
    if not result["totalProjectCost"]:
        result["totalProjectCost"] = sum(value for value in result.values() if isinstance(value, (int, float)))
    return result


def _funding_sources(study: FeasibilityStudyV2) -> Dict[str, Any]:
    pf_loan = sum(tranche.amount or 0 for tranche in study.financing if tranche.priority in {"senior", "middle", "junior", "other"})
    equity = sum(tranche.amount or 0 for tranche in study.financing if tranche.priority == "equity")
    bridge = sum(tranche.amount or 0 for tranche in study.financing if tranche.priority == "bridge")
    return {
        "equity": equity,
        "bridgeLoan": bridge,
        "pfLoan": pf_loan,
        "salesRevenue": study.metrics.total_revenue if study.metrics else 0,
    }


def _pf_structure(study: FeasibilityStudyV2) -> List[Dict[str, Any]]:
    return [
        {
            "tranche": tranche.name,
            "name": tranche.priority,
            "amount": tranche.amount,
            "interestRate": tranche.interest_rate,
            "feeRate": tranche.fee_rate,
            "repaymentPriority": _repayment_priority(tranche.priority),
            "maturityMonths": tranche.term_months,
            "repaymentSource": tranche.repayment_source,
        }
        for tranche in study.financing
    ]


def _schedule(study: FeasibilityStudyV2) -> Dict[str, Any]:
    return {
        item.key: {
            "label": item.label,
            "plannedDate": item.planned_date,
            "monthIndex": item.month_index,
        }
        for item in study.schedule
    }


def _scenario_set(study: FeasibilityStudyV2) -> Dict[str, Any]:
    return {
        "scenarioSetName": "FeasibilityStudyV2 scenarios",
        "scenarios": [
            {
                "scenarioId": scenario.scenario_id,
                "label": scenario.label,
                "salesPriceFactor": scenario.sales_price_factor,
                "constructionCostFactor": scenario.construction_cost_factor,
                "interestRateAddition": scenario.interest_rate_addition,
                "salesRate": scenario.sales_rate,
                "delayMonths": scenario.delay_months,
            }
            for scenario in study.scenarios
        ],
    }


def _mapping_diagnostics(
    study: FeasibilityStudyV2,
    product_mix: List[Dict[str, Any]],
    revenue: Dict[str, Any],
    costs: Dict[str, Any],
    funding: Dict[str, Any],
) -> Dict[str, Any]:
    warnings = []
    if not product_mix:
        warnings.append("product_mix_missing")
    if not revenue.get("totalSalesRevenue"):
        warnings.append("total_sales_revenue_missing")
    if not costs.get("totalProjectCost"):
        warnings.append("total_project_cost_missing")
    if not funding.get("pfLoan"):
        warnings.append("pf_loan_missing")
    if not study.schedule:
        warnings.append("schedule_missing")
    return {
        "status": "mapped_with_warnings" if warnings else "ready_for_cash_flow_engine",
        "warnings": warnings,
    }


def _default_asset_profile_summary(asset_type: str) -> Dict[str, Any]:
    return {
        "profileVersion": "asset-calculation-profile-v1",
        "assetTypes": [asset_type],
        "profiles": {
            asset_type: {
                "revenueMode": "manual_review",
                "defaultCollection": "manual",
                "recoveryHaircut": 0.25,
                "ltvRiskThreshold": 0.80,
            }
        },
    }


def _value(value: Any) -> Any:
    return value.value if value is not None and hasattr(value, "value") else None


def _repayment_priority(priority: str) -> int:
    return {
        "senior": 1,
        "middle": 2,
        "junior": 3,
        "bridge": 1,
        "other": 4,
        "equity": 5,
    }.get(priority, 99)
