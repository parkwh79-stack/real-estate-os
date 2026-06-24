from __future__ import annotations

from agents.credit_document_agent import CreditDocumentAgent


def _project(**overrides):
    data = {
        "project": {"name": "테스트 개발사업", "type": "apartment", "developer": "테스트시행", "contractor": "테스트건설"},
        "land": {"address": "서울시 강남구"},
        "feasibility_input": {"total_revenue": 100_000_000_000, "total_cost": 80_000_000_000},
        "pf_structure": [
            {"tranche": "Tr.A", "amount": 50_000_000_000, "interest_rate": 0.06, "fee_rate": 0.01, "all_in_cost": 0.07, "priority": 1},
            {"tranche": "Tr.B", "amount": 20_000_000_000, "interest_rate": 0.09, "fee_rate": 0.02, "all_in_cost": 0.11, "priority": 2},
        ],
        "covenants": {"claim_preservation": ["관리형토지신탁 1순위 우선수익권"], "eod": ["분양률 미달 시 EOD"]},
    }
    data.update(overrides)
    return data


def _labels(ts):
    return [s["label"] for s in ts["sections"]]


def test_main_pf_stage_has_full_pf_sections():
    ts = CreditDocumentAgent().build_term_sheet(_project(), {"pfStructure": {"ltv": 0.5}}, stage="main_pf")
    assert ts["stage"] == "main_pf"
    labels = _labels(ts)
    assert "책임준공" in labels
    assert "분양대금 배분비율" in labels
    assert "준수사항" in labels
    # responsible completion clause is filled from the standard boilerplate
    rc = next(s["value"] for s in ts["sections"] if s["label"] == "책임준공")
    assert "책임준공" in rc and "채무인수" in rc
    assert "본PF" in ts["stageName"]


def test_bridge_stage_has_land_loan_sections():
    ts = CreditDocumentAgent().build_term_sheet(_project(), {"pfStructure": {"ltv": 0.8}}, stage="bridge")
    assert ts["stage"] == "bridge"
    labels = _labels(ts)
    assert "인출동시이행조건" in labels
    assert "담보처분 시 자금집행순위" in labels
    assert "책임준공" not in labels  # bridge has no responsible-completion section
    ltv = next(s["value"] for s in ts["sections"] if s["label"] == "LTV")
    assert "담보감정가" in ltv
    assert "브릿지론" in ts["stageName"]


def test_auto_detect_stage_from_tranche_count():
    main = CreditDocumentAgent().build_term_sheet(_project(), {})
    assert main["stage"] == "main_pf"
    single = CreditDocumentAgent().build_term_sheet(_project(pf_structure=[]), {})
    assert single["stage"] == "bridge"


def test_explicit_stage_hint_in_project_data():
    ts = CreditDocumentAgent().build_term_sheet(_project(term_sheet_stage="브릿지론"), {})
    assert ts["stage"] == "bridge"


def _land_artifacts():
    return {
        "pfStructure": {"ltv": 0.55},
        "landAgentReview": {
            "summary": {
                "pnu": "1168010100109680002",
                "officialLandPrice": 12_000_000,
                "officialLandPriceBaseYear": "2024",
                "totalAreaSqm": 9688.49,
                "parcelCount": 9,
                "totalPurchaseAmount": 41_374_000_000,
            },
            "evidenceIds": ["land-ev-1", "land-ev-2"],
            "sourceLocators": [{"type": "api_row", "ref": "vworld:pnu"}],
        },
        "developerFeasibility": {"analysisType": "developer_and_pf_feasibility"},
    }


def test_term_sheet_is_multi_agent_with_provenance():
    project = _project(land={"address": "대구 수성구 두산동 81-2", "pnu": "x"})
    ts = CreditDocumentAgent().build_term_sheet(project, _land_artifacts(), stage="main_pf")
    by_label = {s["label"]: s for s in ts["sections"]}

    # Land Agent supplies the land overview, tagged with its provenance + evidence.
    pnu = by_label["지번(PNU)"]
    assert pnu["sourceAgent"] == "LandAgent"
    assert "1168010100109680002" in pnu["value"]
    assert pnu["evidence"]["evidenceIds"] == ["land-ev-1", "land-ev-2"]

    price = by_label["공시지가"]
    assert price["sourceAgent"] == "LandAgent"
    assert "만원/㎡" in price["value"] and "2024" in price["value"]

    assert "9,688" in by_label["대지면적 / 연면적"]["value"]

    # Feasibility owns the 사업수지 summary; credit/legal own clauses.
    assert by_label["사업수지 요약"]["sourceAgent"] == "DevelopmentFeasibilityAgent"
    assert by_label["책임준공"]["sourceAgent"] == "LegalReviewAgent"

    # contributions index lists the collaborating agents.
    agents = {c["agent"] for c in ts["contributions"]}
    assert {"LandAgent", "DevelopmentFeasibilityAgent", "CreditReviewAgent"} <= agents


def test_markdown_renders_stage_and_section_table():
    ts = CreditDocumentAgent().build_term_sheet(_project(), {"pfStructure": {"ltv": 0.5}}, stage="main_pf")
    md = ts["markdown"]
    assert md.startswith("# 거래조건서")
    assert "금융단계:" in md
    assert "## 3. 상세 거래조건" in md
    assert "| 구분 | 내용 |" in md
    assert "책임준공" in md
    # no raw newlines leak into table cells (would break markdown rows)
    table_region = md.split("## 3. 상세 거래조건", 1)[1]
    for line in table_region.splitlines():
        if line.startswith("|"):
            assert line.count("|") >= 3
