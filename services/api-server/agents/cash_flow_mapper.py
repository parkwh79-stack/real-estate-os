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
    cost_schedule = _monthly_cost_schedule(study)
    allocation_rules = _collection_allocation_rules(study)

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
        "costSchedule": cost_schedule,
        "collectionAllocationRules": allocation_rules,
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
            "approvalStatus": (raw_import.get("canonicalStatus") or "unapproved"),
            "approval": raw_import.get("approval") or {},
        },
    }


def _monthly_cost_schedule(study: FeasibilityStudyV2) -> Dict[str, Any]:
    """Preserve source-backed monthly cost rows without inventing classifications.

    The canonical workbook import already keeps each Excel column's cash-out
    buckets and source locator.  This mapper only classifies recognizable cost
    labels; unrecognized outflows remain in ``otherCost`` and retain their
    original row keys in ``sourceBreakdown``.
    """
    raw_import = dict(study.raw_import or {})
    approval_status = str(raw_import.get("canonicalStatus") or "candidate")
    rows: List[Dict[str, Any]] = []
    for row in study.monthly_cash_flow:
        cash_out = dict(row.cash_out or {})
        if not cash_out:
            continue
        classified = {
            "landCost": 0.0,
            "constructionCost": 0.0,
            "financialCost": 0.0,
            "otherCost": 0.0,
        }
        source_breakdown: Dict[str, float] = {}
        for key, raw_amount in cash_out.items():
            try:
                amount = abs(float(raw_amount or 0.0))
            except (TypeError, ValueError):
                continue
            source_breakdown[str(key)] = amount
            classified[_cost_bucket(key)] += amount
        if not any(classified.values()):
            continue
        rows.append({
            "monthIndex": row.month_index,
            "period": row.period,
            **classified,
            "sourceBreakdown": source_breakdown,
            "sourceLocator": row.source_locator,
        })
    return {
        "rows": rows,
        "approvalStatus": approval_status,
        "sourceType": "approved_excel_schedule" if approval_status == "approved" else "workbook_schedule_candidate",
        "sourceLocator": _first_source_locator(rows),
    }


def _cost_bucket(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if any(token in text for token in ("land", "site acquisition", "토지", "용지", "매입")):
        return "landCost"
    if any(token in text for token in ("construction", "contractor", "공사", "도급", "기성")):
        return "constructionCost"
    if any(token in text for token in ("finance", "interest", "fee", "금융", "이자", "수수료")):
        return "financialCost"
    return "otherCost"


def _first_source_locator(rows: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    for row in rows:
        locator = row.get("sourceLocator")
        if isinstance(locator, dict) and locator:
            return locator
    return None


def _collection_allocation_rules(study: FeasibilityStudyV2) -> Dict[str, Any]:
    raw_import = dict(study.raw_import or {})
    raw_rules = raw_import.get("collectionAllocationRules")
    if not isinstance(raw_rules, list):
        raw_rules = []
    approval_status = str(raw_import.get("canonicalStatus") or "candidate")
    source_locator = raw_import.get("collectionAllocationSourceLocator")
    return {
        "rules": [dict(item) for item in raw_rules if isinstance(item, dict)],
        "approvalStatus": approval_status,
        "sourceType": "approved_workbook_rule" if approval_status == "approved" else "workbook_rule_candidate",
        "sourceLocator": source_locator,
    }


def _product_mix(study: FeasibilityStudyV2, asset_profiles: Dict[str, Any]) -> List[Dict[str, Any]]:
    imported_schedule = _imported_product_collection_schedule(study.raw_import)
    if not study.product_mix:
        imported_products = imported_schedule.get("products") or []
        if imported_products:
            return [
                _product_row_from_imported_schedule(product, asset_profiles)
                for product in imported_products
            ]
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
    rows_by_asset_type: Dict[str, List[Dict[str, Any]]] = {}
    for row in study.product_mix:
        asset_type = row.asset_type.value
        profile = _profile(asset_profiles, asset_type)
        mapped = {
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
        }
        imported = (
            imported_schedule.get("byProduct", {}).get(row.product_name)
            or imported_schedule.get("byProduct", {}).get(asset_type)
            or imported_schedule.get("byProduct", {}).get(_normalize_asset_type_key(asset_type))
        )
        if imported:
            same_asset_rows = rows_by_asset_type.setdefault(asset_type, [])
            same_asset_rows.append(mapped)
            is_specific_match = str(row.product_name or "") in {
                str(imported.get("productName") or ""),
                str(imported.get("productId") or ""),
            }
            if is_specific_match or len(same_asset_rows) == 1 and sum(
                1 for candidate in study.product_mix if candidate.asset_type.value == asset_type
            ) == 1:
                mapped.update(_collection_fields(imported))
            else:
                # A workbook row keyed only by asset type is an aggregate
                # schedule. Distribute it across the existing product types so
                # the aggregate contract/interim/balance cash is not duplicated.
                candidates = [candidate for candidate in study.product_mix if candidate.asset_type.value == asset_type]
                total_weight = sum(_product_weight(candidate) for candidate in candidates) or 1.0
                mapped.update(_collection_fields(imported, scale=_product_weight(row) / total_weight))
        rows.append(mapped)
    return rows


def _imported_product_collection_schedule(raw_import: Dict[str, Any]) -> Dict[str, Any]:
    scenarios = raw_import.get("productMonthlySalesRateScenarios") if isinstance(raw_import, dict) else None
    if not isinstance(scenarios, list) or not scenarios:
        return {"products": [], "byProduct": {}}
    scenario = next((item for item in scenarios if isinstance(item, dict)), None)
    if not scenario:
        return {"products": [], "byProduct": {}}
    products = [item for item in scenario.get("products", []) if isinstance(item, dict)]
    by_product: Dict[str, Dict[str, Any]] = {}
    for product in products:
        for key in (
            product.get("productType"),
            product.get("assetType"),
            _normalize_asset_type_key(product.get("productType") or product.get("assetType")),
        ):
            if key:
                by_product[str(key)] = product
    return {"products": products, "byProduct": by_product}


def _product_row_from_imported_schedule(product: Dict[str, Any], asset_profiles: Dict[str, Any]) -> Dict[str, Any]:
    asset_type = str(product.get("assetType") or product.get("productType") or "other").lower()
    normalized_asset_type = _normalize_asset_type_key(asset_type)
    row = {
        "productId": product.get("productType") or "imported-product",
        "type": normalized_asset_type,
        "productName": product.get("productType"),
        "units": None,
        "totalSalesRevenue": product.get("productRevenue") or 0,
        **_profile(asset_profiles, normalized_asset_type),
    }
    row.update(_collection_fields(product))
    return row


def _normalize_asset_type_key(value: Any) -> str:
    """Map workbook labels and canonical asset codes to one matching key."""
    text = str(value or "").strip().casefold()
    if any(token in text for token in ("apartment", "apt", "아파트", "공동주택")):
        return "apartment"
    if any(token in text for token in ("officetel", "office", "오피스텔")):
        return "officetel"
    if any(token in text for token in ("commercial", "retail", "상가", "근린생활")):
        return "commercial"
    if any(token in text for token in ("knowledge", "지식산업")):
        return "knowledge_industry_center"
    if any(token in text for token in ("logistics", "물류")):
        return "logistics"
    if any(token in text for token in ("living", "숙박", "생숙")):
        return "serviced_residence"
    return text or "other"


def _product_weight(row: Any) -> float:
    revenue = _value(getattr(row, "revenue", None))
    units = _value(getattr(row, "unit_count", None))
    try:
        if revenue is not None and float(revenue) > 0:
            return float(revenue)
        if units is not None and float(units) > 0:
            return float(units)
    except (TypeError, ValueError):
        pass
    return 1.0


def _collection_fields(product: Dict[str, Any], scale: float = 1.0) -> Dict[str, Any]:
    steps = [step for step in product.get("collectionSteps", []) if isinstance(step, dict)]
    source_locator = product.get("provenance") or product.get("sourceLocator")
    if not source_locator and steps:
        source_locator = steps[0].get("provenance") or steps[0].get("sourceLocator")
    if scale != 1.0:
        scaled_steps = []
        for step in steps:
            scaled = dict(step)
            amounts = step.get("monthlyCollectionAmounts")
            if isinstance(amounts, list):
                scaled["monthlyCollectionAmounts"] = [float(amount or 0) * scale for amount in amounts]
            scaled_steps.append(scaled)
        steps = scaled_steps
    fields = {
        "collectionSteps": steps,
        "monthlyCollectionAmounts": product.get("monthlyCollectionAmounts") or [],
        "monthlyCollectionRates": product.get("monthlyCollectionRates") or [],
        "cumulativeCollectionRates": product.get("cumulativeCollectionRates") or [],
        "collectionSourceLocator": source_locator,
    }
    return {key: value for key, value in fields.items() if value not in (None, [], {})}


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
