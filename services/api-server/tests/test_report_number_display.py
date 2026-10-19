from base64 import b64decode
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
import json
import re
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from agents.report_export_agent import ReportExportAgent


@pytest.mark.parametrize(("value", "expected"), [
    (1234.5, "1,235"), (-1234.5, "-1,235"), (2.5, "3"), (-2.5, "-3"),
    (0.49, "0"), (-0.49, "0"), (-0.0, "0"), (0, "0"),
    (Decimal("123456789012345678901234567890.5"), "123,456,789,012,345,678,901,234,567,891"),
])
def test_display_rounds_half_away_from_zero_without_changing_value(value, expected):
    assert ReportExportAgent.whole_number_text(value) == expected


def _human_text(exported):
    if exported["format"] == "docx":
        with ZipFile(BytesIO(b64decode(exported["content"]))) as archive:
            return "\n".join(ElementTree.fromstring(archive.read("word/document.xml")).itertext())
    if exported["format"] == "pdf":
        return "\n".join(bytes.fromhex(value.decode()).decode("utf-16-be") for value in
                         re.findall(rb"Tm <([0-9A-F]*)> Tj", b64decode(exported["content"])))
    return exported["content"]


def test_all_report_formats_share_integer_display_and_preserve_exact_json_and_sources():
    cell = {"slot": "pf", "label": "PF 계산", "calculationRunId": "run-0.055",
            "value": 1234.5, "currency": "KRW", "unit": "KRW", "unitSource": "captured", "status": "present"}
    report = {
        "sectionOrder": [{"key": "financialTerms", "title": "금융조건", "index": 4},
                         {"key": "summary", "title": "계산 결과", "index": 11}],
        "sections": {
            "financialTerms": {"금리 단위": "0.05는 5%입니다.", "트랜치 조건": [
                {"명칭": "Senior", "금액": 1234.5, "금리(소수)": 0.055,
                 "수수료율(소수)": 0.01, "상환순위 입력": 1}]},
            "summary": {"사업비": -1234.5, "미확인": None, "영": 0,
                        "sales_value_ltv": {"value": 0.555, "status": "watch"},
                        "원본 위치": "수지!A1", "계산 기록": "run-0.055"}},
        "financingBasis": {"runInputHashes": {"pf": "sealed-0.055"}},
        "calculationSnapshots": {"pf": {"inputSnapshot": {"amount": 1234.5, "rate": 0.055}}},
        "calculationReconciliation": {"summary": "입력 차이 있음", "limitation": "원본 입력만 대조",
            "rows": [{"label": "총사업비", "status": "different", "values": [cell,
                {**cell, "slot": "returns", "label": "수익률", "status": "missing", "value": None}]}],
            "sameFamilyChecks": []}}
    record = {"report_id": "id-0.055", "report": report, "report_markdown": "sealed historical text"}
    before = deepcopy(record)
    agent = ReportExportAgent()
    raw = json.loads(agent.export(record, "json")["content"])
    assert raw["report"] == before["report"]
    assert raw["report"]["sections"]["financialTerms"]["트랜치 조건"][0]["금리(소수)"] == 0.055
    for fmt in ("markdown", "html", "pdf", "docx"):
        text = _human_text(agent.export(record, fmt))
        assert "1,235" in text and "-1,235" in text
        assert "약 6%" in text and "1%" in text and "약 56%" in text
        assert "금리(소수)" not in text and "수수료율(소수)" not in text
        assert "1234.5" not in text and "0.055은" not in text and "0.05는" not in text
        assert "run-0.055" in text and "수지!A1" in text
        assert "정확한 값은 JSON" in text
    assert record == before


def test_projection_uses_explicit_percent_semantics_and_never_rounds_identifiers_or_strings():
    source = {"interestRate": -0.005, "feeRate": -0.004, "dscr": 1.5,
              "saleRates": [0.55, 0.555], "projectIrr": {"value": 0.125, "meetsHurdle": True},
              "projectId": 1234.5, "date": "2026.10.19", "sourceHash": "hash-0.555",
              "原本": "1234.5", "missing": None, "zero": 0}
    before = deepcopy(source)
    projected = ReportExportAgent.presentation_value(source)
    assert projected["금리"] == "약 -1%" and projected["수수료율"] == "약 0%"
    assert projected["분양률"] == ["55%", "약 56%"]
    assert projected["사업 IRR"] == {"value": "약 13%", "meetsHurdle": True}
    assert projected["dscr"] == "2"
    assert projected["projectId"] == "1234.5"
    assert projected["date"] == "2026.10.19" and projected["sourceHash"] == "hash-0.555"
    assert projected["原本"] == "1234.5" and projected["missing"] is None and projected["zero"] == "0"
    assert source == before


def test_projection_retains_alias_collisions_and_localizes_saved_allocation_rates():
    source = {"interestRate": 0.055, "interest_rate": 0.06,
              "contractAndInterimToOperating": 0.755, "balanceToRepayment": 1,
              "collectionRate": 0.125}
    assert ReportExportAgent.presentation_value(source) == {
        "금리": "약 6%", "금리 (interest_rate)": "6%", "계약·중도금 운영배분율": "약 76%",
        "잔금 상환배분율": "100%", "수금 비율": "약 13%"}


def test_feasibility_engine_camel_case_ratios_are_percentages_not_rounded_to_zero():
    result = {"profitMargin": 0.25, "profitOnCost": 0.255, "breakEvenSaleRate": 0.625}
    assert ReportExportAgent.presentation_value(result) == {
        "이익률": "25%", "원가 대비 수익률": "약 26%", "손익분기 분양률": "약 63%"}
    assert result == {"profitMargin": 0.25, "profitOnCost": 0.255, "breakEvenSaleRate": 0.625}


def test_sealed_legacy_comparison_markdown_is_not_reformatted_or_mutated():
    markdown = "# 저장 당시 기준\n금리 5.5%, 금액 1,234.5원\n"
    record = {"report": {"reportType": "cash-flow-comparison-review"}, "report_markdown": markdown}
    assert ReportExportAgent().export(record, "markdown")["content"] == markdown
    assert record["report_markdown"] == markdown


def test_nested_cost_shares_and_collection_step_rate_keep_percentage_context_only_in_known_fields():
    source = {"costs": {"costRatio": {"land": .2, "construction": .655, "finance": .1, "other": .045}},
              "collectionSteps": [{"monthIndex": 0, "rate": .125, "amount": 12.5}],
              "land": 12.5, "rate": 12.5}
    projected = ReportExportAgent.presentation_value(source)
    assert projected["costs"]["원가 구성비"] == {"land": "20%", "construction": "약 66%", "finance": "10%", "other": "약 5%"}
    assert projected["collectionSteps"] == [{"monthIndex": "0", "수금 비율": "약 13%", "amount": "13"}]
    assert projected["land"] == "13" and projected["rate"] == "13"
    assert source["costs"]["costRatio"]["construction"] == .655


def test_word_empty_pod_links_are_compact_but_not_treated_as_approved_or_silently_loaded():
    evidence = {"podReviewReadStatus": "unavailable", "podReviewReadMessage": "검토 저장소 조회 실패",
                "links": [{"title": f"검토 절 {i}", "sectionKey": str(i), "linkedRecordCount": 0,
                           "requiredEvidence": ["실제 약정 확인"], "sourceLocatorCoverage": {"linked": 0},
                           "linkedRecords": [], "podAiReviewFindings": [], "podAiReviewBlocking": False}
                          for i in range(20)]}
    before = deepcopy(evidence)
    agent = ReportExportAgent()
    xml = agent._docx_empty_pod_evidence(evidence)
    root = ElementTree.fromstring('<w:body xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' + xml + '</w:body>')
    text = ''.join(root.itertext())
    assert "검토 저장소 조회 실패" in text and "unavailable" in text
    assert "승인을 뜻하지 않습니다" in text
    assert all(f"검토 절 {i}" in text for i in range(20))
    assert "실제 약정 확인" in text and "linkedRecordCount" not in text
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    assert len(root.findall('.//w:tr', ns)) == 21
    assert evidence == before
    evidence["links"][0]["linkedRecords"] = [{"claim": "계약 확인 필요", "sourceLocator": {"cell": "A7"}}]
    detailed = agent._docx_empty_pod_evidence(evidence)
    assert "계약 확인 필요" in detailed and "A7" in detailed


def test_selected_result_default_note_is_display_only_and_json_keeps_frozen_body():
    report = {"sectionOrder": [{"key": "businessFeasibility", "index": 11, "title": "사업성 검토"}],
              "sections": {"businessFeasibility": {"저장된 결과": {"inputs": {"land_cost": 0}}}},
              "calculationSnapshots": {"feasibility": {"inputSnapshot": {"total_cost": 600.5}}}}
    before = deepcopy(report)
    agent = ReportExportAgent()
    for fmt in ("markdown", "html", "pdf", "docx"):
        assert "엔진 기본값" in _human_text(agent.export({"report": report}, fmt))
    assert json.loads(agent.export({"report": report}, "json")["content"])["report"] == before
    assert report == before


def test_word_tables_keep_all_saved_months_and_missing_values_without_summing_collections():
    monthly = [{"monthIndex": month, "period": f"M{month}",
        "cashIn": {"salesCollection": {"contract": 1234.5, "interim": 0, "balance": None}, "totalPfDraw": -0.4},
        "cashOut": {"totalCashOut": month + 0.5}, "netCashFlow": -1234.5, "endingCash": None}
        for month in range(64)]
    collections = [row["cashIn"]["salesCollection"] for row in monthly]
    record = {"report": {"timingBasis": {"schemaVersion": "test"},
        "sectionOrder": [{"key": "summaryCashFlow", "title": "월별 현금흐름", "index": 12},
                         {"key": "salesPaymentSchedule", "title": "분양수금", "index": 10}],
        "sections": {"summaryCashFlow": {"rows": monthly},
                     "salesPaymentSchedule": {"저장된 수금 결과": collections, "수금 입력": [{"raw": 0.055}]}}}}
    before = deepcopy(record)
    exported = ReportExportAgent().export(record, "docx")
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(BytesIO(b64decode(exported["content"]))) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    tables = root.findall('.//w:tbl', ns)
    assert len(tables) == 2
    for table in tables:
        rows = table.findall('w:tr', ns)
        assert len(rows) == 65
        assert rows[0].find('w:trPr/w:tblHeader', ns) is not None
        assert all(row.find('w:trPr/w:cantSplit', ns) is not None for row in rows)
        assert ["".join(row.find('w:tc', ns).itertext()) for row in rows[1:]] == [f"M{i}" for i in range(64)]
    # Keep the full negative 12-digit amounts in the actual Bonri data readable
    # at 9pt, using wide numeric cells within the same portrait page width.
    assert [int(column.get(f"{{{ns['w']}}}w")) for column in tables[0].findall('w:tblGrid/w:gridCol', ns)] == [800, 1638, 1800, 1800, 1800, 1800]
    assert tables[0].find('w:tblPr/w:tblCellMar/w:left', ns).get(f"{{{ns['w']}}}w") == "80"
    cells = ["".join(cell.itertext()) for cell in tables[0].findall('w:tr', ns)[1].findall('w:tc', ns)]
    assert cells == ["M0", "계약 1,235중도 0잔금 —", "0", "1", "-1,235", "—"]
    assert "1,234.5" not in "".join(root.itertext())
    assert record == before
