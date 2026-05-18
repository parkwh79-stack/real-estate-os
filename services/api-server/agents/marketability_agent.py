from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional


PRODUCT_TYPE_LABELS = {
    "apartment": "Apartment",
    "urban_residential": "Urban Residential Housing",
    "officetel_residential": "Residential Officetel",
    "officetel_office": "Office Officetel",
    "mixed_use_residential": "Mixed-use Residential",
    "commercial_retail": "Commercial Retail",
    "knowledge_industry_center": "Knowledge Industry Center",
    "section_office": "Section Office",
    "serviced_residence": "Serviced Residence",
    "other_saleable": "Other Saleable Asset",
    "non_saleable": "Non-saleable Asset",
}

NON_SALEABLE_PROJECT_TYPES = {
    "logistics",
    "hotel",
    "lodging",
    "operating_hotel",
    "rental_office",
    "long_term_rental_office",
    "data_center",
    "rental_housing",
    "senior_housing",
    "healthcare",
}

PRODUCT_TYPE_ALIASES = {
    "apartment": "apartment",
    "residential": "apartment",
    "urban_residential": "urban_residential",
    "officetel": "officetel_residential",
    "officetel_residential": "officetel_residential",
    "officetel_office": "officetel_office",
    "knowledge_industry_center": "knowledge_industry_center",
    "mixed_use": "mixed_use_residential",
    "mixed_use_residential": "mixed_use_residential",
    "commercial": "commercial_retail",
    "commercial_presale": "commercial_retail",
    "commercial_retail": "commercial_retail",
    "section_office": "section_office",
    "serviced_residence": "serviced_residence",
    "strata_sale": "other_saleable",
}

MARKET_AREA_DEFAULTS = {
    "apartment": (1000, 3000, "same district/city"),
    "urban_residential": (1000, 3000, "same district/city"),
    "officetel_residential": (500, 1000, "office catchment"),
    "officetel_office": (500, 1000, "office catchment"),
    "mixed_use_residential": (1000, 3000, "same district/city"),
    "commercial_retail": (300, 500, "retail catchment"),
    "knowledge_industry_center": (3000, 5000, "industrial catchment"),
    "section_office": (500, 1000, "office catchment"),
    "serviced_residence": (500, 1000, "tourism/office catchment"),
    "other_saleable": (1000, 3000, "same district/city"),
}


class MarketabilityAgent:
    def analyze(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        project = project_data.get("project", {})
        classification = self._classify_product_type(project_data)
        applicability = self._determine_applicability(project_data, classification)

        if not applicability["applicable"]:
            return {
                "analysisType": "marketability_analysis_v2",
                "agentName": "Marketability Analysis Agent",
                "applicable": False,
                "applicabilityReason": applicability["reason"],
                "projectType": project.get("type", "unknown"),
                "productType": classification["productType"],
                "assetClass": self._asset_class(classification["productType"]),
                "classificationConfidence": classification["classificationConfidence"],
                "classificationReason": classification["classificationReason"],
                "dataStatus": "not_applicable",
                "dataLimitations": [
                    "This project is not primarily exited through individual unit sale proceeds."
                ],
                "missingRequiredData": [],
                "projectSummary": self._project_summary(project_data, []),
                "productMix": [],
                "marketArea": {},
                "pricing": {},
                "demandAnalysis": {},
                "supplyAnalysis": {},
                "absorptionForecast": {},
                "exitSaleRate": self._exit_sale_rate(project_data),
                "discountTrigger": [],
                "unsoldRecovery": {},
                "score": None,
                "grade": "N/A",
                "summary": "Marketability review skipped because the asset is outside the saleable-asset scope.",
                "checks": [],
                "riskFlags": [],
                "recommendations": [],
                "reportParagraphs": self._empty_report_paragraphs(),
                "dataSources": self._data_sources(project_data),
                "comparables": [],
                "normalizedComparables": [],
                "comparableSummary": _comparable_summary([]),
            }

        product_type = classification["productType"]
        product_mix = self._normalize_product_mix(project_data, product_type)
        market_area = self._define_market_area(project_data, product_type)
        comparables = self._collect_market_comparables(project_data)
        normalized_comparables = self._normalize_comparables(comparables, product_type)
        pricing = self._compare_pricing(project_data, product_mix, normalized_comparables)
        exit_sale_rate = self._exit_sale_rate(project_data)
        demand = self._analyze_demand(project_data, product_type)
        supply = self._analyze_supply_competition(project_data, normalized_comparables)
        absorption = self._forecast_absorption(pricing, supply, demand, exit_sale_rate)
        discount_trigger = self._propose_discount_trigger(project_data, absorption, exit_sale_rate)
        unsold_recovery = self._analyze_unsold_recovery(project_data, product_type, pricing)
        missing_required_data = self._missing_required_data(project_data, product_mix, normalized_comparables, pricing)
        data_sources = self._data_sources(project_data)
        checks = self._score_marketability(pricing, demand, supply, product_mix, absorption, exit_sale_rate)
        score = round(sum(check["weightedScore"] for check in checks), 1)
        risk_flags = self._risk_flags(pricing, supply, demand, product_mix, exit_sale_rate, normalized_comparables)
        risk_flags.extend(self._market_evidence_risk_flags(project_data.get("marketEvidenceReview")))

        return {
            "analysisType": "marketability_analysis_v2",
            "agentName": "Marketability Analysis Agent",
            "applicable": True,
            "applicabilityReason": applicability["reason"],
            "projectType": project.get("type", "unknown"),
            "productType": product_type,
            "assetClass": self._asset_class(product_type),
            "classificationConfidence": classification["classificationConfidence"],
            "classificationReason": classification["classificationReason"],
            "dataStatus": self._data_status(missing_required_data, data_sources),
            "dataLimitations": self._data_limitations(missing_required_data),
            "missingRequiredData": missing_required_data,
            "projectSummary": self._project_summary(project_data, product_mix),
            "productMix": product_mix,
            "marketArea": market_area,
            "pricing": pricing,
            "demandAnalysis": demand,
            "supplyAnalysis": supply,
            "absorptionForecast": absorption,
            "exitSaleRate": exit_sale_rate,
            "discountTrigger": discount_trigger,
            "unsoldRecovery": unsold_recovery,
            "score": score,
            "grade": self._grade(score),
            "summary": self._summary(score),
            "checks": checks,
            "riskFlags": risk_flags,
            "recommendations": self._recommendations(checks, missing_required_data, risk_flags),
            "reportParagraphs": self._report_paragraphs(score, pricing, supply, exit_sale_rate, risk_flags, data_sources),
            "dataSources": data_sources,
            "comparables": comparables,
            "normalizedComparables": normalized_comparables,
            "comparableSummary": _comparable_summary(normalized_comparables),
        }

    def _classify_product_type(self, data: Dict[str, Any]) -> Dict[str, Any]:
        raw_type = str(data.get("project", {}).get("type") or "unknown").strip().lower()
        if raw_type in NON_SALEABLE_PROJECT_TYPES:
            return {
                "productType": "non_saleable",
                "saleable": False,
                "classificationConfidence": 0.9,
                "classificationReason": f"{raw_type} is classified as a lease/operating asset, not a saleable asset.",
            }
        if raw_type in PRODUCT_TYPE_ALIASES:
            product_type = PRODUCT_TYPE_ALIASES[raw_type]
            return {
                "productType": product_type,
                "saleable": True,
                "classificationConfidence": 0.9,
                "classificationReason": f"project.type={raw_type} maps to {PRODUCT_TYPE_LABELS[product_type]}.",
            }

        has_sales_cashflow = bool(data.get("sales_schedule")) and bool(data.get("feasibility_input", {}).get("total_revenue"))
        if has_sales_cashflow:
            return {
                "productType": "other_saleable",
                "saleable": True,
                "classificationConfidence": 0.65,
                "classificationReason": "Sales revenue and collection schedule indicate an individual-sale exit structure.",
            }

        return {
            "productType": "non_saleable",
            "saleable": False,
            "classificationConfidence": 0.5,
            "classificationReason": "No saleable product type or unit-sale exit structure was identified.",
        }

    def _determine_applicability(self, data: Dict[str, Any], classification: Dict[str, Any]) -> Dict[str, Any]:
        project_type = str(data.get("project", {}).get("type") or "").lower()
        if project_type in NON_SALEABLE_PROJECT_TYPES:
            return {
                "applicable": False,
                "reason": "The project is a lease/operating asset and should be reviewed by a leasing or operating-income agent.",
            }
        if classification["saleable"]:
            return {
                "applicable": True,
                "reason": "The project uses individual unit sale proceeds as a PF repayment source.",
            }
        return {
            "applicable": False,
            "reason": "No material individual sale or presale exit source was identified.",
        }

    def _asset_class(self, product_type: str) -> str:
        return PRODUCT_TYPE_LABELS.get(product_type, "Saleable Real Estate")

    def _normalize_product_mix(self, data: Dict[str, Any], product_type: str) -> List[Dict[str, Any]]:
        raw_items = (
            data.get("product_mix")
            or data.get("productMix")
            or data.get("sales_assumptions", {}).get("product_mix")
            or data.get("salesAssumptions", {}).get("productMix")
            or []
        )
        normalized = []
        for index, item in enumerate(raw_items):
            units = _to_float(item.get("units") or item.get("unitCount") or item.get("households")) or 0
            exclusive_py = _to_float(item.get("exclusiveAreaPy") or item.get("exclusive_area_py")) or 0
            contract_py = _to_float(item.get("contractAreaPy") or item.get("contract_area_py")) or exclusive_py
            price_per_py = _to_float(
                item.get("pricePerPy")
                or item.get("price_per_py")
                or item.get("pricePerContractPy")
                or item.get("price_per_contract_py")
            ) or 0
            total_revenue = _to_float(item.get("totalRevenue") or item.get("total_revenue"))
            total_revenue = total_revenue or units * contract_py * price_per_py
            normalized.append({
                "type": item.get("type") or item.get("name") or f"type-{index + 1}",
                "productType": item.get("productType") or product_type,
                "exclusiveAreaPy": exclusive_py,
                "contractAreaPy": contract_py,
                "units": units,
                "pricePerPy": price_per_py,
                "totalRevenue": total_revenue,
                "exclusiveRatio": exclusive_py / contract_py if contract_py else None,
                "evidenceIds": item.get("evidenceIds", []),
            })

        if normalized:
            return normalized

        feasibility = data.get("feasibility_input", {})
        total_revenue = _to_float(feasibility.get("total_revenue")) or 0
        saleable_area_py = (
            _to_float(data.get("sales_assumptions", {}).get("saleable_area_py"))
            or _to_float(data.get("salesAssumptions", {}).get("saleableAreaPy"))
            or _to_float(data.get("architectural_plan", {}).get("saleable_area_py"))
            or _to_float(data.get("architecturalPlan", {}).get("saleableAreaPy"))
            or 0
        )
        proposed_price = (
            _to_float(data.get("market", {}).get("proposedPricePerPy"))
            or _to_float(data.get("sales_assumptions", {}).get("price_per_py"))
            or _to_float(data.get("salesAssumptions", {}).get("pricePerPy"))
            or (total_revenue / saleable_area_py if total_revenue and saleable_area_py else 0)
        )
        if total_revenue or saleable_area_py or proposed_price:
            return [{
                "type": "base",
                "productType": product_type,
                "exclusiveAreaPy": 0,
                "contractAreaPy": saleable_area_py,
                "units": _to_float(data.get("sales_assumptions", {}).get("units") or data.get("salesAssumptions", {}).get("units")) or 0,
                "pricePerPy": proposed_price,
                "totalRevenue": total_revenue,
                "exclusiveRatio": None,
                "evidenceIds": [],
            }]
        return []

    def _project_summary(self, data: Dict[str, Any], product_mix: List[Dict[str, Any]]) -> Dict[str, Any]:
        land = data.get("land", {})
        feasibility = data.get("feasibility_input", {})
        return {
            "projectName": data.get("project", {}).get("name"),
            "address": land.get("address"),
            "pnu": land.get("pnu"),
            "landAreaSqm": land.get("area_sqm"),
            "grossFloorAreaSqm": data.get("architectural_plan", {}).get("gross_floor_area_sqm") or data.get("architecturalPlan", {}).get("grossFloorAreaSqm"),
            "saleableAreaSqm": data.get("architectural_plan", {}).get("saleable_area_sqm") or data.get("architecturalPlan", {}).get("saleableAreaSqm"),
            "totalUnits": sum(item.get("units") or 0 for item in product_mix),
            "totalSalesRevenue": feasibility.get("total_revenue") or sum(item.get("totalRevenue") or 0 for item in product_mix),
        }

    def _define_market_area(self, data: Dict[str, Any], product_type: str) -> Dict[str, Any]:
        primary, secondary, tertiary = MARKET_AREA_DEFAULTS.get(product_type, MARKET_AREA_DEFAULTS["other_saleable"])
        market = data.get("market", {})
        return {
            "primaryRadiusM": market.get("primaryRadiusM", primary),
            "secondaryRadiusM": market.get("secondaryRadiusM", secondary),
            "tertiaryArea": market.get("tertiaryArea", tertiary),
            "administrativeArea": market.get("administrativeArea") or data.get("land", {}).get("address"),
            "marketAreaReason": market.get("marketAreaReason") or f"Default market area for {PRODUCT_TYPE_LABELS.get(product_type)}.",
        }

    def _collect_market_comparables(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        market = data.get("market", {})
        candidates = []
        for key, source_type, category in [
            ("comparables", "manual_upload", "manual_comparable"),
            ("manualComparables", "manual_upload", "manual_comparable"),
            ("manual_comparables", "manual_upload", "manual_comparable"),
            ("transactionComparables", "public_api", "transaction"),
            ("transaction_comparables", "public_api", "transaction"),
            ("presaleComparables", "manual_upload", "presale"),
            ("presale_comparables", "manual_upload", "presale"),
            ("listingComparables", "partner_api", "listing"),
            ("listing_comparables", "partner_api", "listing"),
        ]:
            for item in market.get(key, []) or []:
                enriched = dict(item)
                enriched.setdefault("sourceType", source_type)
                enriched.setdefault("comparableCategory", category)
                enriched.setdefault("marketPayloadKey", key)
                candidates.append(enriched)
        return candidates

    def _normalize_comparables(self, comparables: List[Dict[str, Any]], product_type: str) -> List[Dict[str, Any]]:
        normalized = []
        for index, item in enumerate(comparables):
            raw_price = _to_float(
                item.get("pricePerPy")
                or item.get("price_per_py")
                or item.get("rawPricePerPy")
                or item.get("salePricePerPy")
                or item.get("sale_price_per_py")
                or item.get("listingPricePerPy")
                or item.get("listing_price_per_py")
                or item.get("presalePricePerPy")
                or item.get("presale_price_per_py")
            )
            price_per_sqm = _to_float(item.get("pricePerSqm") or item.get("price_per_sqm"))
            if raw_price is None and price_per_sqm:
                raw_price = price_per_sqm * 3.305785
            total_price = _to_float(item.get("totalPrice") or item.get("total_price"))
            area_py = _to_float(item.get("contractAreaPy") or item.get("areaPy") or item.get("area_py"))
            area_sqm = _to_float(item.get("contractAreaSqm") or item.get("areaSqm") or item.get("area_sqm"))
            if not area_py and area_sqm:
                area_py = area_sqm / 3.305785
            if raw_price is None and total_price and area_py:
                raw_price = total_price / area_py
            if raw_price is None:
                continue

            distance = _to_float(item.get("distanceM") or item.get("distance_m")) or 9999
            same_product = (item.get("productType") or product_type) == product_type
            distance_factor = 1 + min(distance, 3000) / 100000
            product_factor = 1 if same_product else 0.95
            adjusted = raw_price * distance_factor / product_factor
            similarity = max(0.35, min(0.98, (0.95 if same_product else 0.78) - min(distance, 3000) / 10000))
            normalized.append({
                "caseId": item.get("caseId") or item.get("id") or f"COMP-{index + 1:03d}",
                "name": item.get("name") or item.get("projectName") or f"Comparable {index + 1}",
                "productType": item.get("productType") or product_type,
                "distanceM": distance,
                "rawPricePerPy": raw_price,
                "adjustedPricePerPy": round(adjusted),
                "similarityScore": round(similarity, 2),
                "sourceName": item.get("sourceName") or item.get("source") or "manual comparable",
                "sourceType": item.get("sourceType") or "manual_upload",
                "comparableCategory": item.get("comparableCategory") or "manual_comparable",
                "marketPayloadKey": item.get("marketPayloadKey"),
                "priceBasis": item.get("priceBasis") or ("price_per_sqm_converted" if price_per_sqm else "price_per_py"),
                "sourceUrlOrEvidenceId": item.get("sourceUrlOrEvidenceId") or item.get("evidenceId") or item.get("url"),
                "baseDate": item.get("baseDate") or item.get("transactionDate") or item.get("saleDate"),
                "confidence": item.get("confidence", 0.5),
                "sourceLocator": item.get("sourceLocator") or item.get("source_locator") or {},
                "adjustmentReason": [
                    "same product type" if same_product else "product type adjustment",
                    f"distance adjustment based on {int(distance)}m",
                ],
            })
        return normalized

    def _compare_pricing(
        self,
        data: Dict[str, Any],
        product_mix: List[Dict[str, Any]],
        normalized_comparables: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        proposed = _weighted_average_price(product_mix)
        proposed = proposed or _to_float(data.get("market", {}).get("proposedPricePerPy")) or 0
        comparable_prices = [case["adjustedPricePerPy"] for case in normalized_comparables if case.get("adjustedPricePerPy")]
        if not comparable_prices:
            return {
                "proposedPricePerPy": proposed,
                "comparableMinPricePerPy": None,
                "comparableMedianPricePerPy": None,
                "comparableMaxPricePerPy": None,
                "premiumToMedian": None,
                "percentile": None,
                "judgement": "needs_comparable_data",
                "pricingComment": "Comparable transaction or presale data is missing, so proposed pricing cannot be confirmed.",
            }

        comparable_min = min(comparable_prices)
        comparable_median = median(comparable_prices)
        comparable_max = max(comparable_prices)
        premium = (proposed - comparable_median) / comparable_median if proposed and comparable_median else None
        percentile = len([price for price in comparable_prices if proposed >= price]) / len(comparable_prices) if proposed else None
        judgement = _pricing_judgement(premium)
        return {
            "proposedPricePerPy": proposed,
            "comparableMinPricePerPy": comparable_min,
            "comparableMedianPricePerPy": comparable_median,
            "comparableMaxPricePerPy": comparable_max,
            "premiumToMedian": premium,
            "percentile": percentile,
            "judgement": judgement,
            "pricingComment": _pricing_comment(judgement, premium),
            "evidenceIds": [case.get("sourceUrlOrEvidenceId") for case in normalized_comparables if case.get("sourceUrlOrEvidenceId")],
        }

    def _analyze_demand(self, data: Dict[str, Any], product_type: str) -> Dict[str, Any]:
        market = data.get("market", {})
        explicit_level = market.get("demandLevel") or market.get("demand_level")
        if explicit_level:
            score = {"strong": 82, "moderate": 68, "weak": 48}.get(explicit_level, 58)
            level = explicit_level
        else:
            level = "unknown"
            score = 58
        return {
            "demandLevel": level,
            "score": score,
            "keyDemandDrivers": market.get("keyDemandDrivers") or [],
            "weakDemandFactors": market.get("weakDemandFactors") or [],
            "comment": f"Demand evidence is evaluated for {PRODUCT_TYPE_LABELS.get(product_type)}.",
        }

    def _analyze_supply_competition(self, data: Dict[str, Any], normalized_comparables: List[Dict[str, Any]]) -> Dict[str, Any]:
        market = data.get("market", {})
        upcoming = _to_float(market.get("upcomingSupplyUnits") or market.get("upcoming_supply_units")) or 0
        nearby_count = len(normalized_comparables)
        if upcoming > 1000 or nearby_count >= 8:
            level = "high"
            score = 52
        elif upcoming > 300 or nearby_count >= 3:
            level = "moderate"
            score = 68
        elif nearby_count:
            level = "low"
            score = 78
        else:
            level = "unknown"
            score = 58
        return {
            "competitionLevel": level,
            "score": score,
            "nearbySupplyCount": nearby_count,
            "upcomingSupplyUnits": upcoming,
            "keyCompetitors": normalized_comparables[:5],
            "comment": "Competition level is estimated from nearby comparables and upcoming supply.",
        }

    def _forecast_absorption(
        self,
        pricing: Dict[str, Any],
        supply: Dict[str, Any],
        demand: Dict[str, Any],
        exit_sale_rate: Dict[str, Any],
    ) -> Dict[str, Any]:
        base = 0.72
        if pricing.get("judgement") in {"high", "excessive"}:
            base -= 0.12
        elif pricing.get("judgement") in {"reasonable", "underpriced"}:
            base += 0.08
        if supply.get("competitionLevel") == "high":
            base -= 0.10
        elif supply.get("competitionLevel") == "low":
            base += 0.05
        if demand.get("demandLevel") == "strong":
            base += 0.08
        elif demand.get("demandLevel") == "unknown":
            base -= 0.07
        completion = max(0.35, min(0.95, base))
        return {
            "threeMonths": round(completion * 0.30, 2),
            "sixMonths": round(completion * 0.52, 2),
            "twelveMonths": round(completion * 0.78, 2),
            "completion": round(completion, 2),
            "baseCase": {
                "completionSalesRate": round(completion, 2),
                "exitCoverage": completion >= exit_sale_rate.get("base", 0),
            },
            "downsideCase": {"completionSalesRate": round(max(0, completion - 0.15), 2)},
            "stressCase": {"completionSalesRate": round(max(0, completion - 0.28), 2)},
            "comment": "Baseline absorption uses price competitiveness, demand evidence, competition, and exit-sale-rate pressure.",
        }

    def _exit_sale_rate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        feasibility = data.get("feasibility_input", {})
        total_revenue = feasibility.get("total_revenue", 0) or 0
        total_pf = sum(tranche.get("amount", 0) or 0 for tranche in data.get("pf_structure", []))
        required_repayment = data.get("required_repayment_amount") or data.get("requiredRepaymentAmount") or total_pf

        def rate(discount: float) -> float:
            adjusted_revenue = total_revenue * (1 - discount)
            return required_repayment / adjusted_revenue if adjusted_revenue else 0

        sorted_tranches = sorted(data.get("pf_structure", []), key=lambda item: item.get("priority", 999))
        cumulative = 0
        tranche_rates: Dict[str, float] = {}
        tranche_names = ["senior", "mezzanine", "junior"]
        for index, tranche in enumerate(sorted_tranches[:3]):
            cumulative += tranche.get("amount", 0) or 0
            tranche_rates[tranche_names[index]] = cumulative / total_revenue if total_revenue else 0

        return {
            "base": rate(0),
            "discount5": rate(0.05),
            "discount10": rate(0.10),
            "discount15": rate(0.15),
            "stress": rate(0.20),
            "trancheExitSaleRate": {
                "senior": tranche_rates.get("senior", 0),
                "mezzanine": tranche_rates.get("mezzanine", 0),
                "junior": tranche_rates.get("junior", rate(0)),
            },
        }

    def _propose_discount_trigger(
        self,
        data: Dict[str, Any],
        absorption: Dict[str, Any],
        exit_sale_rate: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        existing = data.get("covenants", {}).get("discount_triggers", [])
        if existing:
            return [
                {
                    "time": f"{item.get('month')} months",
                    "targetSalesRate": item.get("target_rate"),
                    "discountRate": item.get("discount"),
                    "action": "Contracted discount-sale trigger",
                }
                for item in existing
            ]
        completion_target = max(0.8, exit_sale_rate.get("discount10", 0), absorption.get("completion", 0))
        return [
            {"time": "3 months", "targetSalesRate": 0.25, "discountRate": 0, "action": "Strengthen marketing and buyer incentives"},
            {"time": "6 months", "targetSalesRate": 0.40, "discountRate": 0.05, "action": "Review indirect discount up to 5%"},
            {"time": "12 months", "targetSalesRate": 0.60, "discountRate": 0.10, "action": "Review direct discount up to 10%"},
            {"time": "Completion", "targetSalesRate": round(completion_target, 2), "discountRate": 0.15, "action": "Review bulk sale, refinancing, or rental conversion"},
        ]

    def _analyze_unsold_recovery(
        self,
        data: Dict[str, Any],
        product_type: str,
        pricing: Dict[str, Any],
    ) -> Dict[str, Any]:
        total_revenue = data.get("feasibility_input", {}).get("total_revenue", 0) or 0
        haircut = 0.18 if product_type in {"commercial_retail", "serviced_residence"} else 0.12
        unsold_value = total_revenue * (1 - haircut)
        total_pf = sum(tranche.get("amount", 0) or 0 for tranche in data.get("pf_structure", []))
        unsold_ltv = total_pf / unsold_value if unsold_value else 0
        judgement = "acceptable_for_senior" if unsold_ltv <= 0.75 else "weak_for_mezzanine"
        if pricing.get("judgement") in {"high", "excessive"}:
            judgement = "weak_for_mezzanine"
        return {
            "baseUnsoldValue": total_revenue,
            "discountedUnsoldValue": unsold_value,
            "unsoldLTV": unsold_ltv,
            "recoveryJudgement": judgement,
            "comment": "Unsold recovery applies a product-type haircut to total sale revenue.",
        }

    def _score_marketability(
        self,
        pricing: Dict[str, Any],
        demand: Dict[str, Any],
        supply: Dict[str, Any],
        product_mix: List[Dict[str, Any]],
        absorption: Dict[str, Any],
        exit_sale_rate: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        scores = {
            "location": 75,
            "demand": demand.get("score", 58),
            "supply": supply.get("score", 58),
            "pricing": _pricing_score(pricing),
            "product": _product_score(product_mix),
            "absorption": _ratio_score(absorption.get("completion", 0), 0.55, 0.85),
            "pfExit": 85 if exit_sale_rate.get("base", 1) <= 0.6 else 70 if exit_sale_rate.get("base", 1) <= 0.75 else 50,
        }
        weights = {
            "location": 20,
            "demand": 15,
            "supply": 15,
            "pricing": 20,
            "product": 10,
            "absorption": 10,
            "pfExit": 10,
        }
        titles = {
            "location": "Location Competitiveness",
            "demand": "Demand Base",
            "supply": "Supply / Competition Risk",
            "pricing": "Pricing Adequacy",
            "product": "Product Competitiveness",
            "absorption": "Absorption Speed",
            "pfExit": "PF Exit Stability",
        }
        return [
            {
                "key": key,
                "category": titles[key],
                "score": round(scores[key], 1),
                "maxScore": 100,
                "weight": weights[key],
                "weightedScore": round(scores[key] * weights[key] / 100, 2),
                "grade": self._grade(scores[key]),
                "finding": _finding_for_score(scores[key]),
                "riskLevel": _risk_level(scores[key]),
                "evidenceIds": [],
            }
            for key in weights
        ]

    def _risk_flags(
        self,
        pricing: Dict[str, Any],
        supply: Dict[str, Any],
        demand: Dict[str, Any],
        product_mix: List[Dict[str, Any]],
        exit_sale_rate: Dict[str, Any],
        comparables: List[Dict[str, Any]],
    ) -> List[str]:
        flags = []
        premium = pricing.get("premiumToMedian")
        if premium is None or not comparables:
            flags.append("NO_COMPARABLE_DATA")
        elif premium >= 0.20:
            flags.append("PRICE_PREMIUM_EXCESSIVE")
        elif premium >= 0.10:
            flags.append("PRICE_PREMIUM_HIGH")
        if supply.get("competitionLevel") == "high":
            flags.append("SUPPLY_OVERHANG")
        if demand.get("demandLevel") == "unknown":
            flags.append("DEMAND_EVIDENCE_MISSING")
        if exit_sale_rate.get("base", 0) >= 0.70:
            flags.append("EXIT_RATE_HIGH")
        if exit_sale_rate.get("discount10", 0) >= 0.70:
            flags.append("DISCOUNT_REQUIRED")
        if any((item.get("exclusiveRatio") or 1) < 0.55 for item in product_mix):
            flags.append("LOW_EXCLUSIVE_RATIO")
        return flags

    def _market_evidence_risk_flags(self, market_evidence: Dict[str, Any] | None) -> List[str]:
        if not market_evidence:
            return []
        status = market_evidence.get("status")
        if status in {"needs_review", "blocking_issue"}:
            return ["MARKET_EVIDENCE_NEEDS_REVIEW"]
        if status == "needs_evidence":
            return ["MARKET_EVIDENCE_MISSING"]
        return []

    def _missing_required_data(
        self,
        data: Dict[str, Any],
        product_mix: List[Dict[str, Any]],
        comparables: List[Dict[str, Any]],
        pricing: Dict[str, Any],
    ) -> List[str]:
        missing = []
        if not product_mix:
            missing.append("product_mix")
        if not comparables:
            missing.append("nearby_transaction_or_presale_comparables")
        if pricing.get("judgement") == "needs_comparable_data":
            missing.append("pricing_evidence")
        if not self._data_sources(data) and not comparables:
            missing.append("market_data_source_metadata")
        if not data.get("sales_schedule"):
            missing.append("sales_collection_schedule")
        return missing

    def _data_sources(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        sources = data.get("market", {}).get("data_sources") or data.get("dataSources") or []
        normalized = [_normalize_source(source) for source in sources]
        for case in self._collect_market_comparables(data):
            source = _normalize_source(case)
            if source["sourceUrlOrEvidenceId"] or source["sourceName"] != "unknown":
                normalized.append(source)
        unique = {}
        for source in normalized:
            key = (source["sourceName"], source["sourceUrlOrEvidenceId"], source["baseDate"])
            unique[key] = source
        return list(unique.values())

    def _data_status(self, missing: List[str], sources: List[Dict[str, Any]]) -> str:
        if not missing:
            return "ready"
        if sources:
            return "partial"
        return "needs_comparable_data"

    def _data_limitations(self, missing: List[str]) -> List[str]:
        if not missing:
            return []
        return [f"{item} is missing, so this remains a preliminary marketability review." for item in missing]

    def _grade(self, score: float) -> str:
        if score >= 85:
            return "A"
        if score >= 70:
            return "B"
        if score >= 55:
            return "C"
        if score >= 40:
            return "D"
        return "E"

    def _summary(self, score: float) -> str:
        if score >= 85:
            return "Marketability is strong, subject to evidence confirmation."
        if score >= 70:
            return "Marketability is acceptable, but price, supply, and absorption evidence should be confirmed."
        if score >= 55:
            return "Marketability is moderate and requires pricing, trigger, and unsold recovery safeguards."
        return "Marketability risk is high and the product mix, price, PF size, or exit strategy should be revisited."

    def _recommendations(self, checks: List[Dict[str, Any]], missing: List[str], risk_flags: List[str]) -> List[str]:
        recommendations = []
        for check in checks:
            if check["score"] < 70:
                recommendations.append(f"{check['category']}: {check['finding']}")
        if "NO_COMPARABLE_DATA" in risk_flags or missing:
            recommendations.append("Do not confirm sale price adequacy until comparable evidence and source metadata are attached.")
        if "PRICE_PREMIUM_HIGH" in risk_flags or "PRICE_PREMIUM_EXCESSIVE" in risk_flags:
            recommendations.append("Review sale price adjustment, unit-level pricing, or buyer financing conditions.")
        if "EXIT_RATE_HIGH" in risk_flags:
            recommendations.append("Consider reducing junior exposure, increasing equity, or tightening discount-sale triggers.")
        if "MARKET_EVIDENCE_NEEDS_REVIEW" in risk_flags:
            recommendations.append("Resolve document-derived market evidence reconciliation issues before approving pricing or absorption assumptions.")
        if "MARKET_EVIDENCE_MISSING" in risk_flags:
            recommendations.append("Attach uploaded document evidence or manual comparable fixtures before treating marketability as final.")
        return recommendations or ["Attach comparable evidence, absorption assumptions, and target-sales-rate triggers to the PF review memo."]

    def _report_paragraphs(
        self,
        score: float,
        pricing: Dict[str, Any],
        supply: Dict[str, Any],
        exit_sale_rate: Dict[str, Any],
        risk_flags: List[str],
        data_sources: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        grade = self._grade(score)
        source_note = "Market source metadata is attached." if data_sources else "Primary transaction/presale sources are not attached and further verification is required."
        pricing_comment = pricing.get("pricingComment") or "Comparable evidence is required to judge proposed sale price."
        risk_comment = ", ".join(risk_flags) if risk_flags else "no material risk flag"
        return {
            "marketAnalysis": f"The project receives a preliminary marketability grade of {grade}. Competition is assessed as {supply.get('competitionLevel', 'unknown')}. {source_note}",
            "pricingAnalysis": pricing_comment,
            "businessFeasibility": f"Base exit sale rate is {exit_sale_rate['base']:.2%}; under a 10% discount scenario it rises to {exit_sale_rate['discount10']:.2%}.",
            "exitStrategy": "Target sales-rate covenants and discount-sale triggers should be linked to PF repayment waterfall controls.",
            "discountTrigger": "Recommended trigger checkpoints are 3 months, 6 months, 12 months, and completion.",
            "unsoldRisk": "Unsold recovery should be reviewed separately for senior and mezzanine tranche recoverability.",
            "riskMemo": f"Lender-side risk flags: {risk_comment}.",
            "recommendation": self._summary(score),
        }

    def _empty_report_paragraphs(self) -> Dict[str, str]:
        return {
            "marketAnalysis": "",
            "pricingAnalysis": "",
            "businessFeasibility": "",
            "exitStrategy": "",
            "discountTrigger": "",
            "unsoldRisk": "",
            "riskMemo": "",
            "recommendation": "",
        }


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _weighted_average_price(product_mix: List[Dict[str, Any]]) -> float:
    total_area = sum((item.get("contractAreaPy") or 0) * (item.get("units") or 0) for item in product_mix)
    if not total_area:
        total_revenue = sum(item.get("totalRevenue") or 0 for item in product_mix)
        area = sum(item.get("contractAreaPy") or 0 for item in product_mix)
        return total_revenue / area if total_revenue and area else 0
    total = sum((item.get("pricePerPy") or 0) * (item.get("contractAreaPy") or 0) * (item.get("units") or 0) for item in product_mix)
    return total / total_area if total else 0


def _pricing_judgement(premium: Optional[float]) -> str:
    if premium is None:
        return "needs_comparable_data"
    if premium < -0.10:
        return "underpriced"
    if premium <= 0.05:
        return "reasonable"
    if premium <= 0.10:
        return "slightly_high"
    if premium <= 0.20:
        return "high"
    return "excessive"


def _pricing_comment(judgement: str, premium: Optional[float]) -> str:
    if premium is None:
        return "Comparable evidence is missing, so pricing cannot be confirmed."
    premium_text = f"{premium:.1%}"
    comments = {
        "underpriced": f"Proposed price is {premium_text} below comparable median and may indicate conservative pricing or missing premium factors.",
        "reasonable": f"Proposed price is {premium_text} versus comparable median and appears within a reasonable band.",
        "slightly_high": f"Proposed price is {premium_text} above comparable median and requires clear location or product premium evidence.",
        "high": f"Proposed price is {premium_text} above comparable median and should be supported by price adjustment or buyer financing conditions.",
        "excessive": f"Proposed price is {premium_text} above comparable median and creates material sales risk.",
    }
    return comments.get(judgement, "Pricing evidence requires further review.")


def _pricing_score(pricing: Dict[str, Any]) -> float:
    scores = {
        "underpriced": 78,
        "reasonable": 84,
        "slightly_high": 72,
        "high": 58,
        "excessive": 38,
        "needs_comparable_data": 55,
    }
    return scores.get(pricing.get("judgement"), 55)


def _product_score(product_mix: List[Dict[str, Any]]) -> float:
    if not product_mix:
        return 55
    ratios = [item.get("exclusiveRatio") for item in product_mix if item.get("exclusiveRatio")]
    if ratios and min(ratios) < 0.55:
        return 58
    return 75


def _ratio_score(value: float, low: float, high: float) -> float:
    if value >= high:
        return 85
    if value <= low:
        return 50
    return 50 + (value - low) / (high - low) * 35


def _finding_for_score(score: float) -> str:
    if score >= 85:
        return "strong"
    if score >= 70:
        return "acceptable"
    if score >= 55:
        return "needs support"
    return "high risk"


def _risk_level(score: float) -> str:
    if score >= 70:
        return "low"
    if score >= 55:
        return "moderate"
    return "high"


def _normalize_source(source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sourceName": source.get("sourceName") or source.get("name") or source.get("source") or "unknown",
        "sourceType": source.get("sourceType") or source.get("type") or "manual_upload",
        "comparableCategory": source.get("comparableCategory"),
        "sourceUrlOrEvidenceId": source.get("sourceUrlOrEvidenceId") or source.get("url") or source.get("evidence_id") or source.get("evidenceId"),
        "collectedAt": source.get("collectedAt"),
        "baseDate": source.get("baseDate") or source.get("transactionDate") or source.get("saleDate"),
        "licenseOrUsageNote": source.get("licenseOrUsageNote"),
        "confidence": source.get("confidence", 0.5),
        "sourceLocator": source.get("sourceLocator") or source.get("source_locator") or {},
    }


def _comparable_summary(comparables: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_category: Dict[str, int] = {}
    by_source_type: Dict[str, int] = {}
    for item in comparables:
        category = item.get("comparableCategory") or "unknown"
        source_type = item.get("sourceType") or "unknown"
        by_category[category] = by_category.get(category, 0) + 1
        by_source_type[source_type] = by_source_type.get(source_type, 0) + 1
    return {
        "totalCount": len(comparables),
        "byCategory": by_category,
        "bySourceType": by_source_type,
        "hasManualComparable": by_category.get("manual_comparable", 0) > 0,
        "hasTransactionComparable": by_category.get("transaction", 0) > 0,
        "hasPresaleComparable": by_category.get("presale", 0) > 0,
        "hasListingComparable": by_category.get("listing", 0) > 0,
    }
