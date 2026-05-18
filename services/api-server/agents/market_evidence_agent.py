from __future__ import annotations

import re
from statistics import median
from typing import Any, Dict, List, Optional


class MarketEvidenceAgent:
    """Validate document-derived market evidence before marketability scoring.

    This agent does not decide marketability. Its job is to reconcile uploaded
    document values, feasibility assumptions, product mix, comparables, and rent
    assumptions so the Orchestration Agent can pass a cleaner evidence package
    to MarketabilityAgent.
    """

    def review(
        self,
        project_data: Dict[str, Any],
        developer_feasibility: Dict[str, Any] | None = None,
        feasibility: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        market_payload_patch = self._build_market_payload_patch(project_data)
        merged = self._deep_merge(project_data, market_payload_patch)
        pricing_checks = self._validate_sale_price(merged)
        rent_checks = self._validate_rent(merged)
        evidence_records = self._evidence_records(merged, pricing_checks, rent_checks)
        evidence_summary = self._evidence_summary(merged)
        evidence_summary["evidenceRecordCount"] = len(evidence_records)
        issues = [issue for check in pricing_checks + rent_checks for issue in check.get("issues", [])]
        status = self._status(issues, evidence_summary)

        return {
            "reviewType": "market_evidence_reconciliation_v1",
            "agentName": "Market Evidence Agent",
            "status": status,
            "marketPayloadPatch": market_payload_patch,
            "evidenceSummary": evidence_summary,
            "pricingChecks": pricing_checks,
            "rentChecks": rent_checks,
            "evidenceRecords": evidence_records,
            "issues": issues,
            "agentMessages": self._agent_messages(status, issues, evidence_summary),
            "developerFeasibilityReference": self._developer_reference(developer_feasibility),
            "feasibilityReference": self._feasibility_reference(feasibility),
        }

    def _build_market_payload_patch(self, data: Dict[str, Any]) -> Dict[str, Any]:
        document_evidence = (
            data.get("document_market_evidence")
            or data.get("documentMarketEvidence")
            or data.get("market_evidence")
            or data.get("marketEvidence")
            or {}
        )
        patch: Dict[str, Any] = {}
        market_patch: Dict[str, Any] = {}

        for key in [
            "comparables",
            "transactionComparables",
            "presaleComparables",
            "listingComparables",
            "rentComparables",
            "data_sources",
        ]:
            value = document_evidence.get(key) or document_evidence.get(_camelize(key))
            if value:
                market_patch[key] = value

        if document_evidence.get("proposedPricePerPy"):
            market_patch["proposedPricePerPy"] = document_evidence["proposedPricePerPy"]
        if document_evidence.get("demandLevel"):
            market_patch["demandLevel"] = document_evidence["demandLevel"]
        if document_evidence.get("upcomingSupplyUnits"):
            market_patch["upcomingSupplyUnits"] = document_evidence["upcomingSupplyUnits"]

        product_mix = document_evidence.get("product_mix") or document_evidence.get("productMix")
        if product_mix and not data.get("product_mix") and not data.get("productMix"):
            patch["product_mix"] = product_mix
        if market_patch:
            patch["market"] = self._deep_merge(data.get("market", {}), market_patch)
        return patch

    def _validate_sale_price(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        product_mix = _product_mix(data)
        feasibility_revenue = _to_float(data.get("feasibility_input", {}).get("total_revenue"))
        avg_price_per_sqm = _to_float(data.get("sales_assumptions", {}).get("avg_price_per_sqm"))
        proposed_price_per_py = _weighted_price_per_py(product_mix)
        if not proposed_price_per_py:
            proposed_price_per_py = _to_float(data.get("market", {}).get("proposedPricePerPy"))
        checks: List[Dict[str, Any]] = []

        if product_mix and feasibility_revenue:
            product_revenue = sum(_to_float(item.get("totalRevenue") or item.get("total_revenue")) or _item_revenue(item) for item in product_mix)
            variance = _variance(product_revenue, feasibility_revenue)
            checks.append({
                "key": "product_mix_revenue_vs_feasibility",
                "status": "pass" if abs(variance or 0) <= 0.02 else "needs_review",
                "sourceValue": feasibility_revenue,
                "parsedValue": product_revenue,
                "variance": variance,
                "issues": [] if abs(variance or 0) <= 0.02 else [{
                    "severity": "warning",
                    "code": "SALE_REVENUE_RECONCILIATION_GAP",
                    "message": "Product mix revenue differs from feasibility total revenue by more than 2%.",
                }],
            })

        if avg_price_per_sqm and proposed_price_per_py:
            avg_price_per_py = avg_price_per_sqm * 3.305785
            variance = _variance(proposed_price_per_py, avg_price_per_py)
            checks.append({
                "key": "proposed_price_vs_sales_assumption",
                "status": "pass" if abs(variance or 0) <= 0.03 else "needs_review",
                "sourceValue": avg_price_per_py,
                "parsedValue": proposed_price_per_py,
                "variance": variance,
                "issues": [] if abs(variance or 0) <= 0.03 else [{
                    "severity": "warning",
                    "code": "SALE_PRICE_ASSUMPTION_GAP",
                    "message": "Proposed price per py differs from sales assumption average price by more than 3%.",
                }],
            })

        comparables = _comparables(data)
        comparable_prices = [_to_float(item.get("pricePerPy") or item.get("price_per_py") or item.get("rawPricePerPy")) for item in comparables]
        comparable_prices = [value for value in comparable_prices if value]
        if proposed_price_per_py and comparable_prices:
            comparable_median = median(comparable_prices)
            premium = _variance(proposed_price_per_py, comparable_median)
            checks.append({
                "key": "proposed_price_vs_document_comparables",
                "status": "pass" if premium is not None and premium <= 0.10 else "needs_review",
                "sourceValue": comparable_median,
                "parsedValue": proposed_price_per_py,
                "variance": premium,
                "issues": [] if premium is not None and premium <= 0.10 else [{
                    "severity": "warning",
                    "code": "DOCUMENT_COMPARABLE_PRICE_PREMIUM",
                    "message": "Proposed price is more than 10% above document comparable median.",
                }],
            })
        elif proposed_price_per_py and not comparable_prices:
            checks.append({
                "key": "document_comparable_presence",
                "status": "needs_evidence",
                "sourceValue": None,
                "parsedValue": proposed_price_per_py,
                "variance": None,
                "issues": [{
                    "severity": "warning",
                    "code": "NO_DOCUMENT_COMPARABLE_PRICE",
                    "message": "Uploaded documents did not provide comparable sale or presale prices.",
                }],
            })

        return checks

    def _validate_rent(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        rent_assumption = (
            data.get("rental_assumptions")
            or data.get("rentalAssumptions")
            or data.get("market", {}).get("rentalAssumptions")
            or {}
        )
        estimated_rent = _to_float(
            rent_assumption.get("monthlyRentPerPy")
            or rent_assumption.get("monthly_rent_per_py")
            or data.get("market", {}).get("estimatedMonthlyRentPerPy")
        )
        rent_comparables = data.get("market", {}).get("rentComparables") or data.get("market", {}).get("rent_comparables") or []
        rents = [_to_float(item.get("monthlyRentPerPy") or item.get("monthly_rent_per_py") or item.get("rentPerPy")) for item in rent_comparables]
        rents = [value for value in rents if value]
        if not estimated_rent:
            return []
        if not rents:
            return [{
                "key": "rent_comparable_presence",
                "status": "needs_evidence",
                "sourceValue": None,
                "parsedValue": estimated_rent,
                "variance": None,
                "issues": [{
                    "severity": "info",
                    "code": "NO_RENT_COMPARABLE",
                    "message": "Estimated rent exists but uploaded documents did not provide rent comparables.",
                }],
            }]
        rent_median = median(rents)
        variance = _variance(estimated_rent, rent_median)
        return [{
            "key": "estimated_rent_vs_document_comparables",
            "status": "pass" if abs(variance or 0) <= 0.10 else "needs_review",
            "sourceValue": rent_median,
            "parsedValue": estimated_rent,
            "variance": variance,
            "issues": [] if abs(variance or 0) <= 0.10 else [{
                "severity": "warning",
                "code": "RENT_ASSUMPTION_GAP",
                "message": "Estimated rent differs from document rent comparable median by more than 10%.",
            }],
        }]

    def _evidence_summary(self, data: Dict[str, Any]) -> Dict[str, Any]:
        comparables = _comparables(data)
        rent_comparables = data.get("market", {}).get("rentComparables") or data.get("market", {}).get("rent_comparables") or []
        sources = data.get("market", {}).get("data_sources") or data.get("market", {}).get("dataSources") or []
        source_locator_count = sum(
            1
            for item in _product_mix(data) + comparables + rent_comparables + sources
            if _source_locator(item, _primary_evidence_id(item))
        )
        source_ids = [
            item.get("sourceUrlOrEvidenceId") or item.get("evidenceId") or item.get("url")
            for item in comparables + rent_comparables + sources
        ]
        source_ids = [item for item in source_ids if item]
        return {
            "hasProductMix": bool(_product_mix(data)),
            "saleComparableCount": len(comparables),
            "rentComparableCount": len(rent_comparables),
            "sourceMetadataCount": len(sources),
            "evidenceIdCount": len(set(source_ids)),
            "sourceLocatorCount": source_locator_count,
            "hasPageLevelLocators": source_locator_count > 0,
            "hasDocumentMarketEvidence": bool(
                data.get("document_market_evidence")
                or data.get("documentMarketEvidence")
                or data.get("market_evidence")
                or data.get("marketEvidence")
            ),
        }

    def _evidence_records(
        self,
        data: Dict[str, Any],
        pricing_checks: List[Dict[str, Any]],
        rent_checks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        check_status = {
            check["key"]: check["status"]
            for check in pricing_checks + rent_checks
        }

        for index, item in enumerate(_product_mix(data)):
            evidence_id = _primary_evidence_id(item)
            records.append({
                "evidenceId": evidence_id or f"product-mix-{index + 1:03d}",
                "evidenceType": "product_mix",
                "sourceName": item.get("sourceName") or "uploaded document",
                "sourceType": item.get("sourceType") or "manual_upload",
                "sourceUrlOrEvidenceId": evidence_id,
                "baseDate": item.get("baseDate"),
                "confidence": item.get("confidence", 0.6),
                "sourceLocator": _source_locator(item, evidence_id),
                "extractedFields": {
                    "type": item.get("type"),
                    "productType": item.get("productType"),
                    "units": item.get("units"),
                    "contractAreaPy": item.get("contractAreaPy") or item.get("contract_area_py"),
                    "pricePerPy": item.get("pricePerPy") or item.get("price_per_py"),
                    "totalRevenue": item.get("totalRevenue") or item.get("total_revenue"),
                },
                "relatedCheckKeys": ["product_mix_revenue_vs_feasibility"],
                "validationStatus": check_status.get("product_mix_revenue_vs_feasibility", "not_checked"),
                "sourceDocument": _source_document(data),
            })

        for index, item in enumerate(_comparables(data)):
            evidence_id = item.get("sourceUrlOrEvidenceId") or item.get("evidenceId") or item.get("url") or f"sale-comp-{index + 1:03d}"
            records.append({
                "evidenceId": evidence_id,
                "evidenceType": "sale_comparable",
                "sourceName": item.get("sourceName") or item.get("source") or "uploaded document",
                "sourceType": item.get("sourceType") or "manual_upload",
                "sourceUrlOrEvidenceId": evidence_id,
                "baseDate": item.get("baseDate") or item.get("transactionDate") or item.get("saleDate"),
                "confidence": item.get("confidence", 0.6),
                "sourceLocator": _source_locator(item, evidence_id),
                "extractedFields": {
                    "caseId": item.get("caseId"),
                    "name": item.get("name"),
                    "productType": item.get("productType"),
                    "distanceM": item.get("distanceM") or item.get("distance_m"),
                    "pricePerPy": item.get("pricePerPy") or item.get("price_per_py") or item.get("rawPricePerPy"),
                },
                "relatedCheckKeys": ["proposed_price_vs_document_comparables"],
                "validationStatus": check_status.get("proposed_price_vs_document_comparables", "not_checked"),
                "sourceDocument": item.get("sourceDocument") or _source_document(data),
            })

        rent_comparables = data.get("market", {}).get("rentComparables") or data.get("market", {}).get("rent_comparables") or []
        for index, item in enumerate(rent_comparables):
            evidence_id = item.get("sourceUrlOrEvidenceId") or item.get("evidenceId") or item.get("url") or f"rent-comp-{index + 1:03d}"
            records.append({
                "evidenceId": evidence_id,
                "evidenceType": "rent_comparable",
                "sourceName": item.get("sourceName") or item.get("source") or "uploaded document",
                "sourceType": item.get("sourceType") or "manual_upload",
                "sourceUrlOrEvidenceId": evidence_id,
                "baseDate": item.get("baseDate") or item.get("leaseDate"),
                "confidence": item.get("confidence", 0.6),
                "sourceLocator": _source_locator(item, evidence_id),
                "extractedFields": {
                    "caseId": item.get("caseId"),
                    "name": item.get("name"),
                    "productType": item.get("productType"),
                    "distanceM": item.get("distanceM") or item.get("distance_m"),
                    "monthlyRentPerPy": item.get("monthlyRentPerPy") or item.get("monthly_rent_per_py") or item.get("rentPerPy"),
                },
                "relatedCheckKeys": ["estimated_rent_vs_document_comparables"],
                "validationStatus": check_status.get("estimated_rent_vs_document_comparables", "not_checked"),
                "sourceDocument": item.get("sourceDocument") or _source_document(data),
            })

        return records

    def _status(self, issues: List[Dict[str, Any]], evidence_summary: Dict[str, Any]) -> str:
        if any(issue.get("severity") == "blocking" for issue in issues):
            return "blocking_issue"
        if any(issue.get("severity") == "warning" for issue in issues):
            return "needs_review"
        if evidence_summary["saleComparableCount"] == 0 and evidence_summary["rentComparableCount"] == 0:
            return "needs_evidence"
        return "ready_for_marketability_review"

    def _agent_messages(
        self,
        status: str,
        issues: List[Dict[str, Any]],
        evidence_summary: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        messages = {
            "toOrchestrationAgent": [
                f"Market evidence status is {status}.",
                "Pass marketPayloadPatch into MarketabilityAgent before scoring.",
            ],
            "toMarketabilityAgent": [],
            "toFeasibilityAgent": [],
        }
        if issues:
            messages["toOrchestrationAgent"].append("Open market evidence issues should be shown in the orchestration artifact.")
            messages["toMarketabilityAgent"].append("Treat pricing and absorption conclusions as preliminary until issues are resolved.")
        if evidence_summary["saleComparableCount"] == 0:
            messages["toMarketabilityAgent"].append("Comparable sale or presale evidence is missing.")
        if evidence_summary["rentComparableCount"] == 0:
            messages["toMarketabilityAgent"].append("Rent comparable evidence is missing for rent-yield validation.")
        if any(issue.get("code") == "SALE_REVENUE_RECONCILIATION_GAP" for issue in issues):
            messages["toFeasibilityAgent"].append("Reconcile product mix sale revenue with feasibility total revenue.")
        return messages

    def _developer_reference(self, developer_feasibility: Dict[str, Any] | None) -> Dict[str, Any]:
        if not developer_feasibility:
            return {}
        return {
            "status": developer_feasibility.get("pfReadiness", {}).get("status"),
            "confidence": developer_feasibility.get("dataQuality", {}).get("confidence"),
        }

    def _feasibility_reference(self, feasibility: Dict[str, Any] | None) -> Dict[str, Any]:
        if not feasibility:
            return {}
        metrics = feasibility.get("metrics", feasibility)
        return {
            "profitMargin": metrics.get("profit_margin", {}).get("value") if isinstance(metrics.get("profit_margin"), dict) else metrics.get("profit_margin"),
            "status": feasibility.get("status"),
        }

    def _deep_merge(self, base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result


def _product_mix(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (
        data.get("product_mix")
        or data.get("productMix")
        or data.get("sales_assumptions", {}).get("product_mix")
        or data.get("salesAssumptions", {}).get("productMix")
        or []
    )


def _comparables(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    market = data.get("market", {})
    result = []
    for key in ["comparables", "transactionComparables", "transaction_comparables", "presaleComparables", "presale_comparables", "listingComparables", "listing_comparables"]:
        result.extend(market.get(key) or [])
    return result


def _weighted_price_per_py(product_mix: List[Dict[str, Any]]) -> Optional[float]:
    total_area = 0.0
    total_revenue = 0.0
    for item in product_mix:
        units = _to_float(item.get("units") or item.get("unitCount")) or 0
        area = _to_float(item.get("contractAreaPy") or item.get("contract_area_py")) or 0
        price = _to_float(item.get("pricePerPy") or item.get("price_per_py") or item.get("pricePerContractPy")) or 0
        revenue = _to_float(item.get("totalRevenue") or item.get("total_revenue")) or units * area * price
        total_revenue += revenue
        total_area += units * area if units and area else area
    return total_revenue / total_area if total_revenue and total_area else None


def _item_revenue(item: Dict[str, Any]) -> float:
    units = _to_float(item.get("units") or item.get("unitCount")) or 0
    area = _to_float(item.get("contractAreaPy") or item.get("contract_area_py")) or 0
    price = _to_float(item.get("pricePerPy") or item.get("price_per_py") or item.get("pricePerContractPy")) or 0
    return units * area * price


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _variance(parsed_value: Optional[float], source_value: Optional[float]) -> Optional[float]:
    if parsed_value is None or source_value in (None, 0):
        return None
    return (parsed_value - source_value) / source_value


def _camelize(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _source_locator(item: Dict[str, Any], evidence_id: str | None = None) -> Dict[str, Any]:
    raw_locator = (
        item.get("sourceLocator")
        or item.get("source_locator")
        or item.get("locator")
        or item.get("evidenceLocator")
        or item.get("evidence_locator")
    )
    if isinstance(raw_locator, list):
        raw_locator = raw_locator[0] if raw_locator else {}
    if isinstance(raw_locator, dict):
        normalized = {
            "evidenceId": raw_locator.get("evidenceId") or raw_locator.get("evidence_id") or evidence_id,
            "pageNumber": _to_int(
                raw_locator.get("pageNumber")
                or raw_locator.get("page")
                or raw_locator.get("page_no")
                or raw_locator.get("pageNo")
            ),
            "table": raw_locator.get("table") or raw_locator.get("tableName") or raw_locator.get("tableIndex"),
            "row": raw_locator.get("row") or raw_locator.get("rowLabel") or raw_locator.get("rowIndex"),
            "column": raw_locator.get("column") or raw_locator.get("columnLabel") or raw_locator.get("columnIndex"),
            "fieldName": raw_locator.get("fieldName") or raw_locator.get("field") or raw_locator.get("field_name"),
            "textExcerpt": raw_locator.get("textExcerpt") or raw_locator.get("excerpt") or raw_locator.get("sourceText"),
            "confidence": _to_float(raw_locator.get("confidence")),
        }
        if normalized["confidence"] is None:
            normalized["confidence"] = _to_float(item.get("confidence"))
        return {key: value for key, value in normalized.items() if value not in (None, "")}

    inferred = _infer_locator_from_evidence_id(evidence_id)
    if inferred:
        return inferred
    return {}


def _primary_evidence_id(item: Dict[str, Any]) -> str | None:
    evidence_ids = item.get("evidenceIds") or item.get("evidence_ids") or []
    if isinstance(evidence_ids, list) and evidence_ids:
        return evidence_ids[0]
    if isinstance(evidence_ids, str):
        return evidence_ids
    return item.get("sourceUrlOrEvidenceId") or item.get("evidenceId") or item.get("url")


def _infer_locator_from_evidence_id(evidence_id: str | None) -> Dict[str, Any]:
    if not evidence_id:
        return {}
    value = str(evidence_id)
    page_match = re.search(r"(?:^|[-_\s])p(?:age)?[-_\s]?(\d+)", value, flags=re.IGNORECASE)
    table_match = re.search(r"(?:^|[-_\s])table[-_\s]?(\d+)", value, flags=re.IGNORECASE)
    row_match = re.search(r"(?:^|[-_\s])row[-_\s]?(\d+)", value, flags=re.IGNORECASE)
    locator = {"evidenceId": value}
    if page_match:
        locator["pageNumber"] = int(page_match.group(1))
    if table_match:
        locator["table"] = f"table-{table_match.group(1)}"
    if row_match:
        locator["row"] = f"row-{row_match.group(1)}"
    return locator if len(locator) > 1 else {}


def _source_document(data: Dict[str, Any]) -> Dict[str, Any]:
    document_evidence = (
        data.get("document_market_evidence")
        or data.get("documentMarketEvidence")
        or data.get("market_evidence")
        or data.get("marketEvidence")
        or {}
    )
    if isinstance(document_evidence, dict) and isinstance(document_evidence.get("sourceDocument"), dict):
        return document_evidence["sourceDocument"]
    source_documents = data.get("source_documents") or data.get("sourceDocuments") or []
    if source_documents:
        return source_documents[0]
    upload = data.get("_meta", {}).get("upload")
    return upload if isinstance(upload, dict) else {}
