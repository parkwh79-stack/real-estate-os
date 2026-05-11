from typing import Any, Dict, List


SALEABLE_PROJECT_TYPES = {
    "apartment",
    "residential",
    "officetel",
    "knowledge_industry_center",
    "mixed_use",
    "commercial",
}

NON_SALEABLE_PROJECT_TYPES = {
    "logistics",
    "hotel",
    "lodging",
    "rental_office",
}


class MarketabilityAgent:
    def analyze(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        project = project_data.get("project", {})
        project_type = project.get("type", "unknown")
        applicable = self._is_applicable(project_data)

        if not applicable:
            return {
                "applicable": False,
                "projectType": project_type,
                "score": None,
                "grade": "N/A",
                "summary": "분양형 부동산으로 식별되지 않아 별도 분양성 검증을 생략했습니다.",
                "checks": [],
                "recommendations": [],
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

        return {
            "applicable": True,
            "projectType": project_type,
            "assetClass": self._asset_class(project_type),
            "score": score,
            "grade": self._grade(score),
            "summary": self._summary(score),
            "checks": checks,
            "recommendations": self._recommendations(checks),
        }

    def _is_applicable(self, data: Dict[str, Any]) -> bool:
        project_type = data.get("project", {}).get("type")
        if project_type in NON_SALEABLE_PROJECT_TYPES:
            return False
        has_sales_schedule = bool(data.get("sales_schedule"))
        has_sales_revenue = data.get("feasibility_input", {}).get("total_revenue", 0) > 0
        return project_type in SALEABLE_PROJECT_TYPES or (has_sales_schedule and has_sales_revenue)

    def _asset_class(self, project_type: str) -> str:
        labels = {
            "apartment": "아파트",
            "residential": "공동주택",
            "officetel": "오피스텔",
            "knowledge_industry_center": "지식산업센터",
            "mixed_use": "주상복합",
            "commercial": "상업시설",
        }
        return labels.get(project_type, "분양형 부동산")

    def _location_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        address = data.get("land", {}).get("address")
        score = 80 if address else 55
        return self._check("location", "입지 조건", score, "주소 및 입지 정보 확인" if address else "주소/입지 정보 보강 필요")

    def _competition_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        market = data.get("market", {})
        competitors = market.get("competitors")
        score = 78 if competitors and competitors != "자료 미확인" else 58
        return self._check("competition", "경쟁 공급", score, "경쟁단지 정보 반영" if score >= 70 else "경쟁단지/공급 예정 물량 조사 필요")

    def _pricing_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        market = data.get("market", {})
        price_comment = market.get("price_reasonableness")
        score = 76 if price_comment and price_comment != "자료 미확인" else 60
        return self._check("pricing", "분양가 적정성", score, "분양가 코멘트 반영" if score >= 70 else "분양가 비교표 및 평당가 근거 필요")

    def _sales_schedule_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        schedule = data.get("sales_schedule", {})
        total_ratio = sum(schedule.get(key, 0) for key in ["contract_deposit", "middle_payment", "balance"])
        score = 82 if 0.99 <= total_ratio <= 1.01 else 55
        return self._check("payment_schedule", "분양대금 납입 구조", score, f"납입비율 합계 {total_ratio:.2f}")

    def _discount_trigger_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        triggers = data.get("covenants", {}).get("discount_triggers", [])
        score = 80 if triggers else 62
        return self._check("discount_trigger", "할인분양 Trigger", score, "할인분양 조건 존재" if triggers else "목표분양률/할인율 trigger 설정 필요")

    def _exit_resilience_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        total_revenue = data.get("feasibility_input", {}).get("total_revenue", 0)
        total_pf = sum(t.get("amount", 0) for t in data.get("pf_structure", []))
        ltv = total_pf / total_revenue if total_revenue else 0
        if not total_revenue:
            score = 50
            note = "분양수입 자료 미확인"
        elif ltv <= 0.6:
            score = 84
            note = f"분양수입 대비 PF LTV {ltv:.2%}"
        elif ltv <= 0.75:
            score = 70
            note = f"분양수입 대비 PF LTV {ltv:.2%}"
        else:
            score = 55
            note = f"분양수입 대비 PF LTV {ltv:.2%}, 회수 민감도 높음"
        return self._check("exit_resilience", "미분양/Exit 회복력", score, note)

    def _check(self, key: str, title: str, score: float, note: str) -> Dict[str, Any]:
        return {
            "key": key,
            "title": title,
            "score": score,
            "grade": self._grade(score),
            "note": note,
        }

    def _grade(self, score: float) -> str:
        if score >= 80:
            return "A"
        if score >= 70:
            return "B"
        if score >= 60:
            return "C"
        return "D"

    def _summary(self, score: float) -> str:
        if score >= 80:
            return "분양성은 양호하나 실제 경쟁단지 및 분양가 근거 확인이 필요합니다."
        if score >= 70:
            return "분양성은 보통 이상이나 가격/경쟁/흡수율 민감도 검증이 필요합니다."
        return "분양성 보강 검토가 필요하며 목표분양률, 할인분양, 미분양 회수 전략을 재점검해야 합니다."

    def _recommendations(self, checks: List[Dict[str, Any]]) -> List[str]:
        recommendations = []
        for check in checks:
            if check["score"] < 70:
                recommendations.append(f"{check['title']}: {check['note']}")
        return recommendations or ["분양가 비교표, 경쟁단지 흡수율, 목표분양률 trigger 근거를 보고서에 첨부 권장"]
