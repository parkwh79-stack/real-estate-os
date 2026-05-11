from typing import Any, Dict, List


KORAMCO_REFERENCE_MODEL = {
    "source": "koramco_public_site_benchmark",
    "business_lines": [
        {
            "key": "reits",
            "label": "리츠",
            "rationale": "코어오피스, 데이터센터, 물류센터, 리테일, 주거 등 섹터 확장형 운용 플랫폼",
        },
        {
            "key": "fund",
            "label": "부동산펀드",
            "rationale": "대출형, 개발전략형, 블라인드펀드, 산업단지개발 등 전략별 투자 구조",
        },
        {
            "key": "trust",
            "label": "부동산신탁",
            "rationale": "토지개발, 공정관리, 자금관리, 담보/처분/대리사무 기능",
        },
    ],
    "sector_specialties": [
        "office",
        "data_center",
        "logistics",
        "retail",
        "residential",
        "senior_housing",
        "mixed_use_development",
        "overseas_real_estate",
        "structured_trust",
        "urban_redevelopment",
    ],
    "trust_indicators": [
        "aum",
        "equity_capital",
        "trust_assets",
        "track_record",
        "credit_rating",
        "expert_coverage",
        "sector_allocation",
    ],
}


class PlatformBenchmarkAgent:
    """Benchmarks REALYTICS x PLATFORM9 against a full real estate finance platform model.

    The current REALYTICS codebase is strong in PF review/reporting. This agent expands the
    target operating model toward a real estate OS: REITs, funds, trust, sector specialists,
    track record, AUM metrics, and post-closing monitoring.
    """

    def analyze(self, project_data: Dict[str, Any] | None = None) -> Dict[str, Any]:
        project_data = project_data or {}
        project = project_data.get("project", {})
        asset_type = project.get("type", "unknown")

        modules = self._platform_modules(asset_type)
        agents = self._recommended_agents(asset_type)
        gaps = self._capability_gaps(project_data)

        return {
            "benchmark": KORAMCO_REFERENCE_MODEL,
            "targetPositioning": "PF 심사 자동화 앱에서 리츠·펀드·신탁·섹터전략·트랙레코드·모니터링을 포괄하는 부동산 금융 OS로 확장",
            "assetType": asset_type,
            "recommendedPlatformModules": modules,
            "recommendedAgents": agents,
            "capabilityGaps": gaps,
            "roadmap": self._roadmap(),
            "reportAdditions": self._report_additions(),
        }

    def _platform_modules(self, asset_type: str) -> List[Dict[str, Any]]:
        modules = [
            {
                "key": "portfolio_trust_dashboard",
                "priority": "P0",
                "name": "AUM/수탁고/자기자본/트랙레코드 대시보드",
                "description": "개별 PF 건을 넘어 회사/펀드/리츠/신탁 단위의 누적 성과와 신뢰 지표를 보여주는 모듈",
            },
            {
                "key": "business_line_classifier",
                "priority": "P0",
                "name": "리츠·펀드·신탁 사업라인 분류기",
                "description": "프로젝트를 리츠 편입형, 블라인드펀드형, 개발형 펀드, 관리형토지신탁, 차입형토지신탁, 대리사무형으로 자동 분류",
            },
            {
                "key": "sector_strategy_center",
                "priority": "P0",
                "name": "섹터별 투자전략 센터",
                "description": "오피스, 물류, 데이터센터, 리테일, 주거, 시니어하우징, 해외부동산 섹터별 KPI와 심사 기준 관리",
            },
            {
                "key": "track_record_library",
                "priority": "P1",
                "name": "Track Record Library",
                "description": "과거 개발·매입·운용·매각 사례를 구조화해 신규 Deal의 benchmark로 활용",
            },
            {
                "key": "expert_matching",
                "priority": "P1",
                "name": "Expert Matching / Review Routing",
                "description": "자산유형과 리스크 유형에 따라 데이터센터·물류·주거·신탁·해외 담당 Agent 또는 전문가에게 검토 배정",
            },
            {
                "key": "post_closing_monitoring",
                "priority": "P1",
                "name": "Post-Closing Monitoring",
                "description": "공정률, 분양률, 임대율, NOI, LTV, Covenant 위반, EOD Trigger를 사후관리",
            },
        ]

        if asset_type == "logistics":
            modules.append({
                "key": "logistics_sector_scorecard",
                "priority": "P0",
                "name": "물류센터 섹터 Scorecard",
                "description": "IC/항만 접근성, 층고, 도크, 램프, 바닥하중, 저온 스펙, 책임임차인, 선매각 구조를 평가",
            })
        elif asset_type in {"office", "rental_office"}:
            modules.append({
                "key": "office_income_asset_scorecard",
                "priority": "P0",
                "name": "오피스 Income Asset Scorecard",
                "description": "임대율, WALE, NOI, Cap Rate, 임차인 신용도, 리모델링/밸류애드 여지를 평가",
            })
        elif asset_type == "data_center":
            modules.append({
                "key": "data_center_scorecard",
                "priority": "P0",
                "name": "데이터센터 Scorecard",
                "description": "전력용량, 수전가능성, 냉각, 네트워크, 앵커테넌트, 개발 CAPEX, ESG 전력 리스크를 평가",
            })

        return modules

    def _recommended_agents(self, asset_type: str) -> List[Dict[str, Any]]:
        agents = [
            {
                "key": "BusinessLineClassifierAgent",
                "priority": "P0",
                "role": "PF/리츠/펀드/신탁/대리사무/정비사업 중 어느 비즈니스 모델인지 분류",
            },
            {
                "key": "PortfolioMetricsAgent",
                "priority": "P0",
                "role": "AUM, 수탁고, 자기자본, 수익률, Track Record, 섹터 비중을 집계",
            },
            {
                "key": "SectorStrategyAgent",
                "priority": "P0",
                "role": "자산유형별 투자논리, KPI, 리스크 기준, Exit 전략을 생성",
            },
            {
                "key": "TrackRecordBenchmarkAgent",
                "priority": "P1",
                "role": "과거 유사 거래/운용/매각 사례를 찾아 심사보고서와 투자메모에 근거로 연결",
            },
            {
                "key": "ExpertRoutingAgent",
                "priority": "P1",
                "role": "섹터, 리스크, 법률, PF 구조에 따라 검토 담당 Agent와 Human Reviewer를 추천",
            },
            {
                "key": "MonitoringAgent",
                "priority": "P1",
                "role": "승인 후 공정률, 분양률, 임대율, NOI, LTV, EOD Trigger를 추적",
            },
        ]

        sector_agent = {
            "logistics": ("LogisticsAssetAgent", "IC 접근성, 도크, 층고, 저온, 임차인, 선매각 구조를 평가"),
            "data_center": ("DataCenterAssetAgent", "전력, 냉각, 네트워크, 앵커테넌트, CAPEX, ESG 리스크를 평가"),
            "office": ("OfficeAssetAgent", "임대율, WALE, 임차인 신용도, Cap Rate, 밸류애드 전략을 평가"),
            "rental_office": ("OfficeAssetAgent", "임대율, WALE, 임차인 신용도, Cap Rate, 밸류애드 전략을 평가"),
            "residential": ("ResidentialStrategyAgent", "인구·수요·분양성·임대주택/시니어하우징 전략을 평가"),
            "mixed_use": ("MixedUseDevelopmentAgent", "주거·상업·업무 복합비율, 분양성, 임대/매각 복합 Exit를 평가"),
        }.get(asset_type)

        if sector_agent:
            agents.insert(0, {"key": sector_agent[0], "priority": "P0", "role": sector_agent[1]})

        return agents

    def _capability_gaps(self, data: Dict[str, Any]) -> List[Dict[str, str]]:
        gaps = []
        if not data.get("portfolio_metrics"):
            gaps.append({"priority": "P0", "gap": "portfolio_metrics", "recommendation": "AUM, 수탁고, 자기자본, Track Record 집계 스키마 추가"})
        if not data.get("business_line"):
            gaps.append({"priority": "P0", "gap": "business_line", "recommendation": "리츠/펀드/신탁/대리사무/정비사업 분류 필드 추가"})
        if not data.get("sector_strategy"):
            gaps.append({"priority": "P0", "gap": "sector_strategy", "recommendation": "자산유형별 KPI와 투자전략 필드 추가"})
        if not data.get("track_records"):
            gaps.append({"priority": "P1", "gap": "track_records", "recommendation": "유사 거래·개발·운용·매각 사례 라이브러리 추가"})
        if not data.get("monitoring"):
            gaps.append({"priority": "P1", "gap": "monitoring", "recommendation": "승인 이후 공정률/분양률/임대율/Covenant 모니터링 추가"})
        return gaps

    def _roadmap(self) -> List[Dict[str, str]]:
        return [
            {"phase": "Phase 12", "priority": "P0", "title": "Business Line & Sector Strategy Layer", "goal": "PF 심사 중심 구조를 리츠·펀드·신탁·섹터전략 기반 OS로 확장"},
            {"phase": "Phase 13", "priority": "P0", "title": "Portfolio Metrics Dashboard", "goal": "AUM, 수탁고, 자기자본, Track Record, 섹터 비중을 대시보드화"},
            {"phase": "Phase 14", "priority": "P1", "title": "Track Record Benchmark Library", "goal": "유사 거래 사례를 심사보고서 근거로 자동 연결"},
            {"phase": "Phase 15", "priority": "P1", "title": "Expert Routing & Monitoring", "goal": "섹터별 Agent 배정과 승인 후 사후관리 자동화"},
        ]

    def _report_additions(self) -> List[Dict[str, str]]:
        return [
            {"section": "플랫폼 신뢰지표", "content": "AUM, 자기자본, 수탁고, 신용등급, Track Record 요약"},
            {"section": "사업라인 적합성", "content": "본건이 리츠, 펀드, 신탁, 대리사무, 정비사업 중 어느 구조에 적합한지 판단"},
            {"section": "섹터 전문성 검토", "content": "자산유형별 KPI와 전문 검토 항목 반영"},
            {"section": "유사 Track Record", "content": "과거 유사 거래/운용/매각 사례와 차이점 비교"},
            {"section": "승인 후 Monitoring Plan", "content": "공정률, 분양률, 임대율, NOI, LTV, EOD Trigger 사후관리 항목"},
        ]
