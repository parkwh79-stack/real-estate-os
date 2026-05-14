from typing import Any, Dict, List


PRODUCT_TYPE_LABELS = {
    "apartment": "아파트",
    "urban_residential": "도시형생활주택",
    "officetel_residential": "주거용 오피스텔",
    "officetel_office": "업무용 오피스텔",
    "mixed_use_residential": "주상복합",
    "commercial_retail": "상업시설",
    "knowledge_industry_center": "지식산업센터",
    "section_office": "섹션오피스",
    "serviced_residence": "생활형숙박시설",
    "other_saleable": "기타 분양형 부동산",
    "non_saleable": "비분양형 자산",
}

SALEABLE_PROJECT_TYPES = {
    "apartment",
    "residential",
    "urban_residential",
    "officetel",
    "officetel_residential",
    "officetel_office",
    "knowledge_industry_center",
    "mixed_use",
    "mixed_use_residential",
    "commercial",
    "commercial_presale",
    "commercial_retail",
    "section_office",
    "serviced_residence",
    "other_saleable",
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


class MarketabilityAgent:
    def analyze(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        project = project_data.get("project", {})
        project_type = project.get("type", "unknown")
        classification = self._classify_product_type(project_data)
        applicability = self._determine_applicability(project_data, classification)

        if not applicability["applicable"]:
            return {
                "analysisType": "marketability_analysis_v2",
                "agentName": "Marketability Analysis Agent",
                "applicable": False,
                "applicabilityReason": applicability["reason"],
                "projectType": project_type,
                "productType": classification["productType"],
                "assetClass": self._asset_class(classification["productType"]),
                "classificationConfidence": classification["classificationConfidence"],
                "classificationReason": classification["classificationReason"],
                "dataStatus": "not_applicable",
                "dataLimitations": ["분양수입 또는 개별 매각수입을 주요 Exit 재원으로 사용하는 구조가 아닙니다."],
                "missingRequiredData": [],
                "score": None,
                "grade": "N/A",
                "summary": "분양형 부동산으로 식별되지 않아 별도 분양성 검증을 생략했습니다.",
                "checks": [],
                "recommendations": [],
                "reportParagraphs": self._empty_report_paragraphs(),
                "dataSources": self._data_sources(project_data),
            }

        checks = [
            self._location_check(project_data),
            self._competition_check(project_data),
            self._pricing_check(project_data),
            self._sales_schedule_check(project_data),
            self._discount_trigger_check(project_data),
            self._exit_resilience_check(project_data),
        ]
        score = round(sum(check["score"] for check in checks) / len(checks), 1)
        missing_required_data = self._missing_required_data(project_data)
        data_sources = self._data_sources(project_data)
        exit_sale_rate = self._exit_sale_rate(project_data)

        return {
            "analysisType": "marketability_analysis_v2",
            "agentName": "Marketability Analysis Agent",
            "applicable": True,
            "applicabilityReason": applicability["reason"],
            "projectType": project_type,
            "productType": classification["productType"],
            "assetClass": self._asset_class(classification["productType"]),
            "classificationConfidence": classification["classificationConfidence"],
            "classificationReason": classification["classificationReason"],
            "dataStatus": self._data_status(missing_required_data, data_sources),
            "dataLimitations": self._data_limitations(missing_required_data),
            "missingRequiredData": missing_required_data,
            "score": score,
            "grade": self._grade(score),
            "summary": self._summary(score),
            "checks": checks,
            "exitSaleRate": exit_sale_rate,
            "reportParagraphs": self._report_paragraphs(score, checks, project_data, exit_sale_rate),
            "recommendations": self._recommendations(checks, missing_required_data),
            "dataSources": data_sources,
        }

    def _classify_product_type(self, data: Dict[str, Any]) -> Dict[str, Any]:
        raw_type = str(data.get("project", {}).get("type") or "unknown").strip().lower()
        if raw_type in NON_SALEABLE_PROJECT_TYPES:
            return {
                "productType": "non_saleable",
                "saleable": False,
                "classificationConfidence": 0.9,
                "classificationReason": f"{raw_type}는 임대/운영/물류형 자산으로 분류되었습니다.",
            }
        if raw_type in PRODUCT_TYPE_ALIASES:
            product_type = PRODUCT_TYPE_ALIASES[raw_type]
            return {
                "productType": product_type,
                "saleable": True,
                "classificationConfidence": 0.9,
                "classificationReason": f"project.type={raw_type} 기준으로 {PRODUCT_TYPE_LABELS[product_type]} 상품으로 분류했습니다.",
            }

        sales_schedule = data.get("sales_schedule")
        total_revenue = data.get("feasibility_input", {}).get("total_revenue", 0)
        if sales_schedule and total_revenue:
            return {
                "productType": "other_saleable",
                "saleable": True,
                "classificationConfidence": 0.65,
                "classificationReason": "분양대금 납입 일정과 분양수입이 있어 기타 분양형 자산으로 조건부 분류했습니다.",
            }

        return {
            "productType": "non_saleable",
            "saleable": False,
            "classificationConfidence": 0.5,
            "classificationReason": "분양형 상품유형 또는 분양수입 구조가 확인되지 않았습니다.",
        }

    def _determine_applicability(self, data: Dict[str, Any], classification: Dict[str, Any]) -> Dict[str, Any]:
        project_type = data.get("project", {}).get("type")
        if project_type in NON_SALEABLE_PROJECT_TYPES:
            return {
                "applicable": False,
                "reason": "장기 임대, 운영수익, 물류/호텔형 자산으로 분류되어 별도 Agent 검토 대상입니다.",
            }
        if classification["saleable"]:
            return {
                "applicable": True,
                "reason": "개별 분양 또는 매각수입을 PF 상환재원으로 사용하는 구조로 판단됩니다.",
            }
        return {
            "applicable": False,
            "reason": "분양수입 또는 개별 매각수입이 주요 Exit 재원인지 확인되지 않았습니다.",
        }

    def _asset_class(self, product_type: str) -> str:
        return PRODUCT_TYPE_LABELS.get(product_type, "분양형 부동산")

    def _location_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        address = data.get("land", {}).get("address")
        score = 80 if address else 55
        note = "주소 및 입지 정보 확인" if address else "주소/PNU/입지 정보 보강 필요"
        return self._check("location", "입지 조건", score, note)

    def _competition_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        market = data.get("market", {})
        competitors = market.get("competitors") or market.get("comparables")
        score = 78 if competitors and competitors != "자료 미확인" else 58
        note = "경쟁사례 정보 반영" if score >= 70 else "경쟁단지/공급 예정 물량 조사 필요"
        return self._check("competition", "경쟁 공급", score, note)

    def _pricing_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        market = data.get("market", {})
        price_comment = market.get("price_reasonableness")
        score = 76 if price_comment and price_comment != "자료 미확인" else 60
        note = "분양가 코멘트 반영" if score >= 70 else "분양가 비교표 및 평당가 근거 필요"
        return self._check("pricing", "분양가 적정성", score, note)

    def _sales_schedule_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        schedule = data.get("sales_schedule", {})
        total_ratio = sum(schedule.get(key, 0) for key in ["contract_deposit", "middle_payment", "balance"])
        score = 82 if 0.99 <= total_ratio <= 1.01 else 55
        return self._check("payment_schedule", "분양대금 납입 구조", score, f"납입비율 합계 {total_ratio:.2f}")

    def _discount_trigger_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        triggers = data.get("covenants", {}).get("discount_triggers", [])
        score = 80 if triggers else 62
        note = "할인분양 조건 존재" if triggers else "목표분양률/할인율 trigger 설정 필요"
        return self._check("discount_trigger", "할인분양 Trigger", score, note)

    def _exit_resilience_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        total_revenue = data.get("feasibility_input", {}).get("total_revenue", 0)
        exit_rate = self._exit_sale_rate(data)["base"]
        if not total_revenue:
            score = 50
            note = "분양수입 자료 미확인"
        elif exit_rate <= 0.6:
            score = 84
            note = f"Base Exit 분양률 {exit_rate:.2%}"
        elif exit_rate <= 0.75:
            score = 70
            note = f"Base Exit 분양률 {exit_rate:.2%}"
        else:
            score = 55
            note = f"Base Exit 분양률 {exit_rate:.2%}, 회수 민감도 높음"
        return self._check("exit_resilience", "미분양/Exit 회복력", score, note)

    def _exit_sale_rate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        feasibility = data.get("feasibility_input", {})
        total_revenue = feasibility.get("total_revenue", 0) or 0
        total_pf = sum(tranche.get("amount", 0) or 0 for tranche in data.get("pf_structure", []))
        required_repayment = data.get("required_repayment_amount") or total_pf

        def rate(discount: float) -> float:
            adjusted_revenue = total_revenue * (1 - discount)
            return required_repayment / adjusted_revenue if adjusted_revenue else 0

        sorted_tranches = sorted(data.get("pf_structure", []), key=lambda item: item.get("priority", 999))
        cumulative = 0
        tranche_rates: Dict[str, float] = {}
        tranche_names = ["senior", "mezzanine", "junior"]
        for index, tranche in enumerate(sorted_tranches[:3]):
            cumulative += tranche.get("amount", 0) or 0
            name = tranche_names[index]
            tranche_rates[name] = cumulative / total_revenue if total_revenue else 0

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

    def _missing_required_data(self, data: Dict[str, Any]) -> List[str]:
        missing = []
        market = data.get("market", {})
        if not market.get("comparables") and not market.get("competitors"):
            missing.append("nearby_transaction_or_presale_comparables")
        if not market.get("price_reasonableness"):
            missing.append("pricing_evidence")
        if not self._data_sources(data):
            missing.append("market_data_source_metadata")
        if not data.get("sales_schedule"):
            missing.append("sales_collection_schedule")
        return missing

    def _data_sources(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        sources = data.get("market", {}).get("data_sources") or data.get("dataSources") or []
        normalized = []
        for source in sources:
            normalized.append({
                "sourceName": source.get("sourceName") or source.get("name", "unknown"),
                "sourceType": source.get("sourceType") or source.get("type", "manual_upload"),
                "sourceUrlOrEvidenceId": source.get("sourceUrlOrEvidenceId") or source.get("url") or source.get("evidence_id"),
                "collectedAt": source.get("collectedAt"),
                "baseDate": source.get("baseDate"),
                "licenseOrUsageNote": source.get("licenseOrUsageNote"),
                "confidence": source.get("confidence", 0.5),
            })
        return normalized

    def _data_status(self, missing: List[str], sources: List[Dict[str, Any]]) -> str:
        if not missing:
            return "ready"
        if sources:
            return "partial"
        return "needs_comparable_data"

    def _data_limitations(self, missing: List[str]) -> List[str]:
        if not missing:
            return []
        return [f"{item} 자료가 없어 확정 판단이 아닌 예비 검토 결과입니다." for item in missing]

    def _check(self, key: str, title: str, score: float, note: str) -> Dict[str, Any]:
        return {
            "key": key,
            "title": title,
            "score": score,
            "grade": self._grade(score),
            "note": note,
        }

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
            return "분양성은 우수하나 실제 경쟁단지, 분양가, 흡수율 근거 확인은 필요합니다."
        if score >= 70:
            return "분양성은 양호하나 가격, 경쟁공급, 흡수율 민감도 검증이 필요합니다."
        if score >= 55:
            return "분양성은 보통 수준이며 분양가, 할인분양 Trigger, 미분양 회수 전략 보완이 필요합니다."
        return "분양위험이 높아 상품구성, 분양가, PF 규모, Exit 전략 재검토가 필요합니다."

    def _recommendations(self, checks: List[Dict[str, Any]], missing: List[str]) -> List[str]:
        recommendations = []
        for check in checks:
            if check["score"] < 70:
                recommendations.append(f"{check['title']}: {check['note']}")
        if missing:
            recommendations.append("비교사례, 기준일, 데이터 출처를 보강하기 전까지 분양가 적정성을 확정하지 않습니다.")
        return recommendations or ["분양가 비교표, 경쟁단지 흡수율, 목표분양률 trigger 근거를 보고서에 첨부 권장"]

    def _report_paragraphs(
        self,
        score: float,
        checks: List[Dict[str, Any]],
        data: Dict[str, Any],
        exit_sale_rate: Dict[str, Any],
    ) -> Dict[str, str]:
        pricing = next((check for check in checks if check["key"] == "pricing"), None)
        competition = next((check for check in checks if check["key"] == "competition"), None)
        grade = self._grade(score)
        data_sources = self._data_sources(data)
        source_note = "데이터 출처가 확인되었습니다." if data_sources else "주변 실거래가·분양사례 출처가 없어 추가 검증이 필요합니다."
        return {
            "marketAnalysis": f"본건의 분양성 등급은 {grade}로 산정되었습니다. {competition['note'] if competition else ''} {source_note}",
            "pricingAnalysis": f"{pricing['note'] if pricing else '제안 분양가 비교자료가 필요합니다.'}",
            "businessFeasibility": f"Base Exit 분양률은 {exit_sale_rate['base']:.2%}, 10% 할인분양 시 {exit_sale_rate['discount10']:.2%}로 산정됩니다.",
            "exitStrategy": "목표 분양률 미달 시 단계적 할인분양 Trigger와 분양수입금 상환계좌 적립 조건을 약정에 반영해야 합니다.",
            "discountTrigger": "분양개시 후 6개월, 12개월, 준공 시점 기준 목표 분양률과 할인율 Trigger를 설정하는 방안을 검토합니다.",
            "unsoldRisk": "미분양 발생 시 선순위 회수 가능성과 중순위 이하 Tranche 회수위험을 별도로 점검해야 합니다.",
            "riskMemo": "경쟁공급, 제안 분양가 프리미엄, 데이터 출처 부족은 대주단 관점의 핵심 분양 리스크입니다.",
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
