from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, List

from core.calculations import normalize_tranche_priority
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
    period_basis = _cash_flow_period_basis(study)

    request = {
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
        **({"periodBasis": period_basis} if period_basis is not None else {}),
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
    captured = _captured_workbook_cash_flow(study, request)
    if captured is not None:
        request["capturedWorkbookCashFlow"] = captured
        request.update(analysisOnly=True, reportEligible=False)
        request["sourceMetadata"].update(analysisOnly=True, reportEligible=False)
        diagnostics["warnings"].append("captured_workbook_cash_flow_basis_differs_from_business_totals")
        diagnostics["status"] = "mapped_with_warnings"
    return request


def captured_cash_flow_binding(request: Dict[str, Any]) -> str:
    """Detect stale source replay when any mapped financial input is edited."""
    payload = {key: request.get(key) for key in (
        "sourcesAndUses", "productMix", "pfStructure", "schedule",
        "costSchedule", "collectionAllocationRules", "periodBasis", "monthlyFinancing", "scenarioSet",
    )}
    payload["project"] = {key: (request.get("project") or {}).get(key)
                          for key in ("buildingUse", "location", "landAreaSqm", "grossFloorAreaSqm")}
    capture = request.get("capturedWorkbookCashFlow")
    payload["capturedSourceContentHash"] = captured_cash_flow_content_hash(capture) if isinstance(capture, dict) else None
    def financial_only(value: Any) -> Any:
        if isinstance(value, list):
            return [financial_only(item) for item in value]
        if isinstance(value, dict):
            return {key: financial_only(item) for key, item in value.items()
                    if key.replace("_", "").casefold() not in {
                        "approvalstatus", "approval", "canonicalstatus", "authoritystatus", "reporteligible",
                    }}
        return value
    payload = financial_only(payload)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def captured_cash_flow_content_hash(capture: dict) -> str:
    payload = {key: value for key, value in capture.items() if key not in {"bindingHash", "contentHash"}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def captured_study_financial_binding(study_data: Any) -> str:
    study = study_data if isinstance(study_data, FeasibilityStudyV2) else FeasibilityStudyV2.model_validate(study_data)
    payload = study.model_dump(mode="json", exclude={"raw_import"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _captured_workbook_cash_flow(study: FeasibilityStudyV2, request: dict) -> dict | None:
    raw = study.raw_import or {}
    profile = raw.get("profile")
    if profile not in {"sangbong-sale-feasibility", "sangbong-private-rental-feasibility"} or not study.monthly_cash_flow:
        return None
    request["mappingDiagnostics"]["warnings"].append("captured_cash_flow_source_unavailable")
    source_binding = raw.get("sourceFinancialInputHash")
    if source_binding != captured_study_financial_binding(study):
        request["mappingDiagnostics"]["warnings"].append("captured_cash_flow_source_inputs_changed_or_unbound")
        return None
    review = raw.get("cashFlowReview") or {}
    source = raw.get("sourceDocument") or {}
    digest = source.get("originalFileHash")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        return None
    basis = request.get("periodBasis") or {}
    rows = [row.model_dump(mode="json") for row in study.monthly_cash_flow]
    checks = review.get("periodChecks") or []
    if not (review.get("transactionMappingComplete") is True and review.get("calendarVerified") is True
            and review.get("periodControlsMatched") is True and basis.get("kind") == "calendar_monthly"
            and 1 <= len(rows) <= 240 and len(checks) == len(rows) == len(basis.get("periods") or [])):
        return None
    if any(row.get("month_index") != index or not (row.get("source_locator") or {}).get("complete")
           or (row.get("source_locator") or {}).get("transactionRowPolicy") != "sangbong-cash-flow-map-v1"
           or check.get("sourcePeriodIndex") != index or check.get("passed") is not True
           or check.get("cumulativePassed") is not True
           for index, (row, check) in enumerate(zip(rows, checks, strict=True))):
        return None
    request["mappingDiagnostics"]["warnings"].remove("captured_cash_flow_source_unavailable")
    capture = {
        "schemaVersion": "captured-workbook-cash-flow-v1", "sourceProfile": profile,
        "originalFileHash": digest, "sourceName": raw.get("sourceWorkbook"),
        "sourceFinancialInputHash": source_binding,
        "rows": rows, "periodChecks": copy.deepcopy(checks),
        "sourceControls": copy.deepcopy(review.get("sourceControls") or []),
        "warnings": copy.deepcopy(review.get("warnings") or []),
        "businessDeclaredTotals": study.metrics.model_dump(mode="json") if study.metrics else {},
        "sourceBasis": "captured_workbook_cash_flow", "analysisOnly": True, "reportEligible": False,
    }
    capture["contentHash"] = captured_cash_flow_content_hash(capture)
    capture["bindingHash"] = captured_cash_flow_binding({**request, "capturedWorkbookCashFlow": capture})
    return capture


def _cash_flow_period_basis(study: FeasibilityStudyV2) -> Dict[str, Any] | None:
    """Project only the source timeline actually used by this request.

    Working budget drafts can copy import context without copying source rows.
    Their unrelated monthly analysis must not inherit the source-event guard.
    Older Bonri captures can disclose event sequence, but cannot reconstruct the
    omitted numeric headers or elapsed dates from display strings alone.
    """
    if not study.monthly_cash_flow:
        return None
    raw_import = dict(study.raw_import or {})
    captured = raw_import.get("cashFlowPeriodBasis")
    if isinstance(captured, dict):
        return copy.deepcopy(captured)
    sample_profile = raw_import.get("sampleProfile")
    profile = raw_import.get("profile") or (sample_profile.get("templateId") if isinstance(sample_profile, dict) else None)
    if not str(profile or "").startswith("legacy-bonri-mixed-use-pf-feasibility"):
        return None
    event_labels = {"토지계약", "교평등", "사업승인", "분양"}
    if not any(row.period.strip() in event_labels for row in study.monthly_cash_flow):
        return None
    periods = []
    for row in study.monthly_cash_flow:
        source = row.source_locator or {}
        periods.append({
            "sourcePeriodIndex": row.month_index,
            "label": row.period,
            "periodKind": "event" if row.period.strip() in event_labels else "unknown",
            "sourceMonthNumber": None,
            # Only captured locators are retained. The old transaction range
            # does not prove which original header cell supplied a month.
            "sourceLocator": {
                "sourceName": source.get("sourceDocument") or source.get("sourceName") or raw_import.get("sourceWorkbook"),
                "sheet": source.get("sheetName") or source.get("sheet"),
                "cell": source.get("cell"),
            },
        })
    return {
        "schemaVersion": "cash-flow-period-basis-v1", "kind": "source_sequence", "periods": periods,
        "scheduleBindings": {event.key: {"sourcePeriodIndex": None, "sourceMonthNumber": None} for event in study.schedule},
        "warnings": [
            "이전에 저장된 원본 사건 순서만 확인됩니다. 월 번호 헤더와 일정 대응은 원본 파일을 다시 가져와 확인해 주세요.",
            "사전 사건의 실제 날짜와 경과 기간은 미확인입니다. 원본 열 순번을 한 달씩의 기간으로 계산하지 않습니다.",
        ],
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
    excluded_financing_transfers: List[Dict[str, Any]] = []
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
            if _is_financing_transfer_key(key):
                excluded_financing_transfers.append({
                    "monthIndex": row.month_index,
                    "sourceKey": str(key),
                    "amount": amount,
                    "reason": "financing_transfer_not_project_cost",
                })
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
        "excludedFinancingTransfers": excluded_financing_transfers,
    }


def _cost_bucket(value: Any) -> str:
    text = str(value or "").strip().casefold()
    row_number = _legacy_row_number(text)
    if row_number is not None:
        if 58 <= row_number <= 62:
            return "constructionCost"
        if 63 <= row_number <= 68:
            return "landCost"
        if 97 <= row_number <= 110:
            return "financialCost"
        if 70 <= row_number <= 92 or 112 <= row_number <= 119:
            return "otherCost"
    if any(token in text for token in ("land", "site acquisition", "토지", "용지", "매입")):
        return "landCost"
    if any(token in text for token in ("construction", "contractor", "공사", "도급", "기성")):
        return "constructionCost"
    if any(token in text for token in ("finance", "interest", "fee", "금융", "이자", "수수료")):
        return "financialCost"
    return "otherCost"


def _legacy_row_number(value: Any) -> int | None:
    match = re.match(r"^row_(\d+)_", str(value or "").strip().casefold())
    return int(match.group(1)) if match else None


def _is_financing_transfer_key(value: Any) -> bool:
    row_number = _legacy_row_number(value)
    if row_number is not None and (93 <= row_number <= 96 or row_number in {108, 111}):
        return True
    text = str(value or "").strip().casefold()
    return any(token in text for token in (
        "loan_repayment",
        "debt_repayment",
        "equity_recovery",
        "equity_distribution",
        "대출금상환",
        "차입금상환",
        "투자자금회수",
    ))


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
    # A declared total is a fallback, not another line item.  A workbook may
    # split sales into private housing, public housing and commercial receipts.
    # Each source line must contribute once, including an explicit zero.
    grouped: Dict[str, List[Any]] = {key: [] for key in (
        "totalSalesRevenue", "leaseDeposit", "forwardSaleProceeds", "refinancingProceeds", "otherRevenue"
    )}
    for item in study.revenue_items:
        grouped[_revenue_bucket(item.category)].append(item)
    result = {
        key: None if any(item.amount is None for item in items) else sum(item.amount for item in items)
        for key, items in grouped.items()
    }
    if not grouped["totalSalesRevenue"]:
        result["totalSalesRevenue"] = study.metrics.total_revenue if study.metrics else 0
    return result


def _revenue_bucket(category: str) -> str:
    if category in {"sales", "sales_revenue"}:
        return "totalSalesRevenue"
    if category in {"lease", "lease_deposit"}:
        return "leaseDeposit"
    if category in {"forward_sale", "sale_proceeds"}:
        return "forwardSaleProceeds"
    if category in {"refinancing", "refinancing_loan"}:
        return "refinancingProceeds"
    return "otherRevenue"


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
    pf_loan = sum(
        tranche.amount or 0
        for tranche in study.financing
        if tranche.priority in {"senior", "mezzanine", "junior", "other"}
    )
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
            "trancheId": tranche.tranche_id,
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
        if tranche.priority != "equity"
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
    if revenue.get("totalSalesRevenue") is None:
        warnings.append("total_sales_revenue_missing")
    missing_revenue_items = [
        {"key": item.key, "category": item.category, "bucket": _revenue_bucket(item.category)}
        for item in study.revenue_items if item.amount is None
    ]
    if missing_revenue_items:
        warnings.append("revenue_line_items_incomplete")
    if not costs.get("totalProjectCost"):
        warnings.append("total_project_cost_missing")
    if not funding.get("pfLoan"):
        warnings.append("pf_loan_missing")
    if not study.schedule:
        warnings.append("schedule_missing")
    return {
        "status": "mapped_with_warnings" if warnings else "ready_for_cash_flow_engine",
        "warnings": warnings,
        **({"missingRevenueItems": missing_revenue_items} if missing_revenue_items else {}),
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
    return normalize_tranche_priority(priority)
