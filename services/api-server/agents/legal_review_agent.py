from typing import Any, Dict, List


class LegalReviewAgent:
    def review(
        self,
        project_data: Dict[str, Any],
        law_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        law_context = law_context or {}
        checks = [
            self._zoning_check(project_data),
            self._permit_check(project_data),
            self._land_rights_check(project_data),
            self._trust_security_check(project_data),
            self._covenant_check(project_data),
            self._law_context_check(law_context),
        ]
        blocking = [check for check in checks if check["severity"] == "blocking"]
        caution = [check for check in checks if check["severity"] == "caution"]

        return {
            "reviewType": "korean_real_estate_pf_legal_precheck",
            "mcp": {
                "requested": True,
                "connected": bool(law_context),
                "source": law_context.get("source", "not_connected"),
                "note": "Korean Law MCP가 연결되면 law_context의 조항/판례/인허가 근거를 검토 결과에 병합합니다.",
            },
            "status": "blocking_issue" if blocking else "caution" if caution else "clear",
            "checks": checks,
            "lawReferences": law_context.get("references", []),
            "requiredFollowUps": self._follow_ups(blocking, caution),
            "disclaimer": "본 결과는 PF 심사용 예비 법률/인허가 체크이며, 최종 법률의견서 또는 인허가 가능성 확약이 아닙니다.",
        }

    def _zoning_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        regulation = data.get("regulation", {})
        legal_far = regulation.get("legal_far", 0)
        applied_far = regulation.get("applied_far", 0)
        legal_bcr = regulation.get("legal_bcr", 0)
        applied_bcr = regulation.get("applied_bcr", 0)
        issues = []
        if legal_far and applied_far and applied_far > legal_far:
            issues.append("적용 용적률이 법정 용적률을 초과합니다.")
        if legal_bcr and applied_bcr and applied_bcr > legal_bcr:
            issues.append("적용 건폐율이 법정 건폐율을 초과합니다.")
        if not regulation:
            issues.append("용도지역, 법정 용적률/건폐율 자료가 없습니다.")
        return self._check(
            "zoning",
            "용도지역/용적률/건폐율",
            "blocking" if any("초과" in issue for issue in issues) else "caution" if issues else "clear",
            issues or ["규제 수치상 초과 항목은 식별되지 않았습니다."],
        )

    def _permit_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        schedule = data.get("schedule", {})
        permit = schedule.get("permit_approval")
        construction = schedule.get("construction_start")
        issues = []
        if not permit:
            issues.append("사업승인/건축허가 일정이 없습니다.")
        if construction and not permit:
            issues.append("착공 일정은 있으나 인허가 완료 일정이 누락되었습니다.")
        return self._check(
            "permit",
            "사업승인/건축허가/착공 전제",
            "caution" if issues else "clear",
            issues or ["인허가 일정이 사업 일정에 반영되어 있습니다."],
        )

    def _land_rights_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        parcels = data.get("land_acquisition", [])
        issues = []
        if not parcels:
            issues.append("필지별 소유자/지분율/제한물권 자료가 없습니다.")
        share_sum = sum(p.get("share", 0) for p in parcels)
        if parcels and share_sum < 0.999:
            issues.append(f"확보 지분율 합계가 {share_sum:.2%}로 100% 미만입니다.")
        mortgages = [p.get("mortgage") for p in parcels if p.get("mortgage")]
        if mortgages:
            issues.append("근저당권 등 제한물권 말소 또는 신탁 전 정리 조건 확인이 필요합니다.")
        return self._check(
            "land_rights",
            "사업부지 권리확보",
            "blocking" if parcels and share_sum < 0.999 else "caution" if issues else "clear",
            issues or ["사업부지 지분율과 권리관계 자료가 확인되었습니다."],
        )

    def _trust_security_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        claims = data.get("covenants", {}).get("claim_preservation", [])
        has_trust = any("신탁" in item or "우선수익" in item for item in claims)
        issues = [] if has_trust else ["관리형토지신탁, 우선수익권 등 채권보전 구조 확인이 필요합니다."]
        return self._check(
            "trust_security",
            "신탁/우선수익권/채권보전",
            "caution" if issues else "clear",
            issues or ["신탁 또는 우선수익권 관련 채권보전 항목이 반영되어 있습니다."],
        )

    def _covenant_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        covenants = data.get("covenants", {})
        missing = []
        for key, label in [
            ("drawdown_conditions", "인출 선행조건"),
            ("eod", "기한이익상실 사유"),
            ("execution_order", "자금집행 및 변제충당 순위"),
        ]:
            if not covenants.get(key):
                missing.append(label)
        return self._check(
            "covenants",
            "약정/Covenant/EOD",
            "caution" if missing else "clear",
            [f"누락 항목: {', '.join(missing)}"] if missing else ["주요 약정 항목이 반영되어 있습니다."],
        )

    def _law_context_check(self, law_context: Dict[str, Any]) -> Dict[str, Any]:
        if not law_context:
            return self._check(
                "korean_law_mcp",
                "Korean Law MCP 근거 조회",
                "caution",
                ["현재 실행 환경에서 Korean Law MCP 연결 근거가 주입되지 않았습니다."],
            )
        references = law_context.get("references", [])
        return self._check(
            "korean_law_mcp",
            "Korean Law MCP 근거 조회",
            "clear" if references else "caution",
            ["외부 법령/인허가 근거가 연결되었습니다."] if references else ["law_context는 있으나 references가 비어 있습니다."],
        )

    def _check(self, key: str, title: str, severity: str, findings: List[str]) -> Dict[str, Any]:
        return {
            "key": key,
            "title": title,
            "severity": severity,
            "findings": findings,
        }

    def _follow_ups(self, blocking: List[Dict[str, Any]], caution: List[Dict[str, Any]]) -> List[str]:
        follow_ups = []
        for check in blocking + caution:
            follow_ups.append(f"{check['title']}: {' / '.join(check['findings'])}")
        return follow_ups or ["법무법인/인허가 컨설턴트 확인 후 최종 의견서 첨부 권장"]
