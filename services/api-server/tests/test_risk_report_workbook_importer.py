from pathlib import Path

from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName

from agents.risk_report_workbook_importer import import_risk_report_workbook


def _build_risk_report_workbook(path: Path) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    report = workbook.create_sheet("리스크보고서첨부")
    metrics = workbook.create_sheet("핵심지표")
    risks = workbook.create_sheet("20대리스크")
    cash_flow = workbook.create_sheet("현금흐름요약")
    source_map = workbook.create_sheet("근거셀매핑")
    contract = workbook.create_sheet("Import계약")

    report.append(["리스크 보고서 첨부용 요약", None, None])
    report.append([])
    report.append(["보고서 삽입 문구", "구분", "문구"])
    report.append(["Cash Flow 검토", "요약", "본건 현금흐름은 PF 인출조건과 상환계좌 배분 구조를 함께 검토해야 합니다."])
    report.append(["승인조건 후보", "금융", "Stress Case에서 선순위 shortfall이 발생하지 않도록 PF 한도 또는 Equity 보강 조건을 반영합니다."])

    metrics.append(["지표", "값", "단위", "산정 기준", "근거"])
    metrics.append(["총분양수입", 1655.97, "억원", "사업수지 집계", "사업수지!D8"])
    metrics.append(["총사업비", 1518.42, "억원", "사업수지 집계", "사업수지!D72"])
    metrics.append(["시행이익", 137.55, "억원", "총분양수입 - 총사업비", "사업수지!D74"])
    metrics.append(["시행이익률", 0.083, "%", "시행이익 / 총분양수입", "계산"])
    metrics.append(["PF 대출금", 900, "억원", "Tr.A/B/C 합계", "2.현금흐름!48:55"])
    metrics.append(["Base Exit 분양률", 0.5435, "%", "PF 원금 회수 기준", "계산"])
    metrics.append(["Tr.A Exit 분양률", 0.5917, "%", "선순위 회수 기준", "계산"])
    metrics.append(["Tr.B Exit 분양률", 0.6696, "%", "중순위 회수 기준", "계산"])
    metrics.append(["Tr.C Exit 분양률", 0.768, "%", "후순위 회수 기준", "계산"])

    risks.append(["No", "리스크 구분", "관리 항목", "위험도", "담당", "필요 증빙", "근거 시트", "근거 위치", "보고서 섹션", "보고서 반영 방식"])
    risks.append([1, "토지", "토지 소유권 확보", "보통", "토지/법무", "매매계약서", "토지조서", "토지조서!A2:H10", "사업부지 확보", "토지 확보 현황 확인"])
    risks.append([2, "Exit", "미분양 LTV", "중대", "금융", "Unsold LTV Matrix", "핵심지표", "핵심지표!B10", "Exit 전략", "미분양 환가위험 검토"])

    cash_flow.append(["구분", "Cash-In", "Cash-Out", "Net CF", "누적 CF", "샘플 근거", "리스크 보고서 반영"])
    cash_flow.append(["Base Case", 1655.97, 1518.42, 137.55, 137.55, "사업수지!D8:D74", "사업성 요약"])

    source_map.append(["필드", "원천", "위치", "정밀도", "비고"])
    source_map.append(["총분양수입", "샘플 사업수지", "사업수지!D8", "excel_cell", "보고서 사업성 요약"])

    contract.append(["Key", "Value", "Note"])
    contract.append(["schema_version", "realytics-risk-report-xlsx-v1", "server import schema"])
    contract.append(["project_id", "p-risk", "project id"])
    contract.append(["project_name", "Risk Import Project", "project name"])
    contract.append(["source_workbook", "sample.xlsx", "source"])
    contract.append(["template_mode", "test", "mode"])
    contract.append(["risk_row_count", 2, "count"])

    named_ranges = {
        "risk_schema_version": "'Import계약'!$B$2",
        "risk_project_id": "'Import계약'!$B$3",
        "risk_project_name": "'Import계약'!$B$4",
        "risk_total_revenue": "'핵심지표'!$B$2",
        "risk_total_project_cost": "'핵심지표'!$B$3",
        "risk_pf_loan": "'핵심지표'!$B$6",
        "risk_base_exit_sale_rate": "'핵심지표'!$B$7",
        "risk_junior_exit_sale_rate": "'핵심지표'!$B$10",
        "risk_control_table": "'20대리스크'!$A$1:$J$3",
    }
    for name, ref in named_ranges.items():
        workbook.defined_names.add(DefinedName(name, attr_text=ref))
    workbook.save(path)


def test_import_risk_report_workbook_builds_register_and_report_sections(tmp_path):
    workbook_path = tmp_path / "risk-report.xlsx"
    _build_risk_report_workbook(workbook_path)

    draft = import_risk_report_workbook(workbook_path, project_id="p-risk")

    assert draft["validation"]["importReady"] is True
    assert draft["metrics"]["totalRevenue"] == 1655.97
    assert draft["riskRegisterDraft"]["summary"]["riskCount"] == 2
    assert draft["riskRegisterDraft"]["risks"][1]["severity"] == "critical"
    assert draft["riskRegisterDraft"]["risks"][1]["sourceLocators"][1]["path"] == "핵심지표!B10"
    assert draft["reportSectionsDraft"]["paragraphs"]
    assert draft["reportSectionsDraft"]["approvalConditions"]
    assert draft["sourceMap"][0]["locator"] == "사업수지!D8"
