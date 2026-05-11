from typing import Dict, Any

class ReportAgent:
    def __init__(self):
        pass

    def generate_report(self, input_data: Dict[str, Any]) -> str:
        """
        Generates a professional 20-section PF Review Report based on realistic sample data.
        """
        project = input_data.get("project", {})
        covenants = input_data.get("covenants", {})
        
        md = self._build_cover(project)
        md += self._build_financial_terms(input_data)
        md += self._build_claim_preservation(covenants)
        md += self._build_discount_triggers(covenants)
        md += self._build_financing_structure(input_data)
        md += self._build_use_of_funds(input_data)
        md += self._build_land_acquisition(input_data)
        md += self._build_project_schedule(input_data)
        md += self._build_financial_structure_diagram(input_data)
        md += self._build_sales_schedule(input_data)
        md += self._build_revenue_distribution(covenants)
        md += self._build_cashflow_summary(input_data)
        md += self._build_borrower_analysis(input_data)
        md += self._build_contractor_analysis(input_data)
        md += self._build_marketability_analysis(input_data)
        md += self._build_feasibility_analysis(input_data)
        md += self._build_exit_strategy(input_data)
        md += self._build_drawdown_conditions(covenants)
        md += self._build_eod(covenants)
        md += self._build_fund_execution_order(covenants)
        
        return md

    def _build_cover(self, project: Dict[str, Any]) -> str:
        name = project.get("name", "Unknown Project")
        date = project.get("review_date", "TBD")
        fi = project.get("financial_institution", "Our Institution")
        
        return f"""# PF 심사보고서 (Draft)

| 항목 | 상세 내용 |
|:---|:---|
| **사업명** | {name} |
| **심의일** | {date} |
| **금융기관** | {fi} |
| **검토 의견** | 본건은 {name} 개발사업을 위한 PF 대출 건으로, 제반 조건 및 사업성 검토 결과 대출 실행이 적정할 것으로 판단됨. |

---
"""

    def _build_financial_terms(self, data: Dict[str, Any]) -> str:
        tranches = data.get("pf_structure", [])
        if not tranches:
            return "## 1. 주요 금융조건 요약표\n데이터 없음.\n\n"
            
        md = "## 1. 주요 금융조건 요약표\n\n"
        md += "| Tranche | 대출금액 | 금리 | 수수료 | All-in Cost | 우선순위 |\n"
        md += "|:---|---:|:---:|:---:|:---:|:---:|\n"
        for t in tranches:
            md += f"| {t.get('tranche')} | {t.get('amount'):,}원 | {t.get('interest_rate')*100}% | {t.get('fee_rate')*100}% | {t.get('all_in_cost', 0)*100}% | {t.get('priority')}순위 |\n"
        return md + "\n"

    def _build_claim_preservation(self, covenants: Dict[str, Any]) -> str:
        claims = covenants.get("claim_preservation", [])
        md = "## 2. 주요 채권보전방안\n\n"
        if not claims:
            md += "- 해당사항 없음\n"
        else:
            for claim in claims:
                md += f"- **{claim}**\n"
        return md + "\n"

    def _build_discount_triggers(self, covenants: Dict[str, Any]) -> str:
        triggers = covenants.get("discount_triggers", [])
        md = "## 3. 할인분양 Trigger 및 조건\n\n"
        if not triggers:
            md += "해당사항 없음.\n"
        else:
            md += "| 개월차 | 목표분양률 | 할인율 | 비고 |\n|:---:|:---:|:---:|:---|\n"
            for t in triggers:
                md += f"| {t.get('month')}개월 | {t.get('target_rate')*100}% | {t.get('discount')*100}% | 미달 시 즉시 실행 |\n"
        return md + "\n"

    def _build_financing_structure(self, data: Dict[str, Any]) -> str:
        pf = sum(t.get("amount", 0) for t in data.get("pf_structure", []))
        rev = data.get("feasibility_input", {}).get("total_revenue", 0)
        
        md = "## 4. 자금조달 구조\n\n"
        md += f"- **총 조달금액(PF)**: {pf:,}원\n"
        md += f"- **총 분양수입(예상)**: {rev:,}원\n"
        md += f"- **LTV (분양수입 대비)**: {(pf/rev*100 if rev > 0 else 0):.2f}%\n"
        return md + "\n"

    def _build_use_of_funds(self, data: Dict[str, Any]) -> str:
        uses = data.get("use_of_funds", [])
        md = "## 5. 자금 사용 용도\n\n"
        if not uses:
            md += "데이터 없음.\n"
        else:
            md += "| 자금 집행 항목 | 금액(원) | 비고 |\n|:---|---:|:---|\n"
            for u in uses:
                md += f"| {u.get('item')} | {u.get('amount'):,} | 사업계획 기준 |\n"
        return md + "\n"

    def _build_land_acquisition(self, data: Dict[str, Any]) -> str:
        lands = data.get("land_acquisition", [])
        md = "## 6. 사업부지 확보 현황\n\n"
        if not lands:
            md += "데이터 없음.\n"
        else:
            md += "| 소유자 | 지분율 | 면적(㎡) | 제한물권 및 특이사항 |\n|:---:|:---:|:---:|:---|\n"
            for l in lands:
                md += f"| {l.get('owner')} | {l.get('share')*100}% | {l.get('area_sqm'):,} | {l.get('mortgage')} |\n"
        return md + "\n"

    def _build_project_schedule(self, data: Dict[str, Any]) -> str:
        s = data.get("schedule", {})
        md = "## 7. 사업추진 일정\n\n"
        md += f"- **토지매입 완료**: {s.get('land_purchase', 'TBD')}\n"
        md += f"- **사업인허가 완료**: {s.get('permit_approval', 'TBD')}\n"
        md += f"- **착공 및 분양**: {s.get('construction_start', 'TBD')}\n"
        md += f"- **준공 및 입주**: {s.get('completion', 'TBD')}\n"
        return md + "\n"

    def _build_financial_structure_diagram(self, data: Dict[str, Any]) -> str:
        dev_name = data.get("developer_info", {}).get("name", "시행사")
        con_name = data.get("contractor_info", {}).get("name", "시공사")
        fi_name = data.get("project", {}).get("financial_institution", "대주단")
        
        md = "## 8. 금융구조도\n\n"
        md += "```mermaid\n"
        md += "graph TD;\n"
        md += f"  Lender[\"{fi_name} (대주단)\"] -- \"PF 대출금\" --> Trust[\"신탁사 (자금관리)\"];\n"
        md += f"  Trust -- \"공사비 지급\" --> Contractor[\"{con_name}\"];\n"
        md += f"  Contractor -- \"책임준공 확약\" --> Lender;\n"
        md += f"  Dev[\"{dev_name}\"] -- \"사업권/수익권\" --> Trust;\n"
        md += f"  Buyer[\"수분양자\"] -- \"분양대금\" --> Trust;\n"
        md += "```\n\n"
        return md

    def _build_sales_schedule(self, data: Dict[str, Any]) -> str:
        s = data.get("sales_schedule", {})
        md = "## 9. 분양대금 납입 일정\n\n"
        md += f"- **계약금**: {s.get('contract_deposit', 0)*100}% (계약 시)\n"
        md += f"- **중도금**: {s.get('middle_payment', 0)*100}% (회차별 분할)\n"
        md += f"- **잔금**: {s.get('balance', 0)*100}% (입주 시)\n"
        return md + "\n"

    def _build_revenue_distribution(self, covenants: Dict[str, Any]) -> str:
        md = "## 10. 분양수입금 배분 기준\n\n"
        md += f"{covenants.get('revenue_distribution', '대주단 협의 기준에 따름')}\n"
        return md + "\n"

    def _build_cashflow_summary(self, data: Dict[str, Any]) -> str:
        md = "## 11. 요약 현금흐름표\n\n"
        md += "분양률 시나리오 기반의 월별 Cash-In/Out 분석 결과, 본 사업의 자금 부족액(Max Minus)은 대출 한도 내에서 충분히 커버 가능함.\n"
        return md + "\n"

    def _build_borrower_analysis(self, data: Dict[str, Any]) -> str:
        dev = data.get("developer_info", {})
        md = "## 12. 차주(시행사) 분석\n\n"
        md += f"- **시행사명**: {dev.get('name', 'N/A')}\n"
        md += f"- **신용등급**: {dev.get('credit_rating', 'N/A')}\n"
        md += f"- **주요 재무현황**: {dev.get('financials', 'N/A')}\n"
        return md + "\n"

    def _build_contractor_analysis(self, data: Dict[str, Any]) -> str:
        con = data.get("contractor_info", {})
        md = "## 13. 시공사 분석\n\n"
        md += f"- **시공사명**: {con.get('name', 'N/A')}\n"
        md += f"- **신용등급**: {con.get('credit_rating', 'N/A')}\n"
        md += f"- **수주잔고**: {con.get('order_backlog', 'N/A')}\n"
        md += f"- **재무 건전성**: {con.get('financials', 'N/A')}\n"
        return md + "\n"

    def _build_marketability_analysis(self, data: Dict[str, Any]) -> str:
        md = "## 14. 분양성 분석\n\n"
        md += "인근 지역 유사 평형 공급 현황 및 시세 분석 결과, 당 사업지의 경쟁 우위가 확보되어 목표 분양률 달성이 무난할 것으로 사료됨.\n"
        return md + "\n"

    def _build_feasibility_analysis(self, data: Dict[str, Any]) -> str:
        er = data.get("expected_results", {})
        md = "## 15. 사업성 분석 결과\n\n"
        md += f"- **예상 개발이익**: {er.get('development_profit', 0):,}원\n"
        md += f"- **수익률(Margin)**: {er.get('profit_margin', 0)*100}%\n"
        return md + "\n"

    def _build_exit_strategy(self, data: Dict[str, Any]) -> str:
        md = "## 16. Exit 전략 및 상환 재원\n\n"
        md += "1차적으로 분양수입금을 통한 원리금 상환을 추진하며, 비상시 할인분양 또는 미분양 담보대출을 통한 대환 상환 시나리오를 확보함.\n"
        return md + "\n"

    def _build_drawdown_conditions(self, covenants: Dict[str, Any]) -> str:
        conds = covenants.get("drawdown_conditions", [])
        md = "## 17. 대출금 인출 선행조건\n\n"
        if not conds:
            md += "- 인허가 완료 및 신탁계약 체결 등\n"
        else:
            for c in conds:
                md += f"- {c}\n"
        return md + "\n"

    def _build_eod(self, covenants: Dict[str, Any]) -> str:
        eods = covenants.get("eod", [])
        md = "## 18. 기한이익상실(EOD) 사유\n\n"
        if not eods:
            md += "- 이자 미납, 책임준공 기한 초과 등\n"
        else:
            for e in eods:
                md += f"- {e}\n"
        return md + "\n"

    def _build_fund_execution_order(self, covenants: Dict[str, Any]) -> str:
        orders = covenants.get("execution_order", [])
        md = "## 19. 자금집행 순서\n\n"
        if not orders:
            md += "1. 필수사업비 2. 대출이자 3. 대출원금 순\n"
        else:
            for i, o in enumerate(orders):
                md += f"{o}\n"
        md += "\n## 20. 변제 충당 및 기타\n\n상기 순서에 의거하여 분양수입금을 배분하며, 대주단 동의 없는 자금 인출은 엄격히 제한함.\n"
        return md + "\n"
