from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree

import pytest

from agents.report_agent import ReportAgent
from agents.report_export_agent import ReportExportAgent
from routers import projects
from use_cases.cash_flow_comparison import content_hash
from use_cases.report_calculation_basis import attach_cash_flow_basis, attach_calculation_bases
from use_cases.report_financing_basis import TIMING_CF_NOTE, attach_financing_basis
from use_cases.report_timing_basis import CF_SCOPE, SECTION_KEYS, VERSION, attach_timing_basis, verify_timing_basis
from test_cash_flow_comparison import record
from test_pf_report_calculation_bases import feasibility_record
from test_pf_report_cash_flow_basis import selected_run


def report_with_cf():
    run = record()
    report = ReportAgent().generate_structured_report({})
    attach_cash_flow_basis(report, run)
    return report


@pytest.mark.parametrize("schedule", [
    {"construction_start": {"plannedDate": None, "monthIndex": 0, "label": "착공", "sourceCell": "A5"},
     "completion": {"plannedDate": "2029-03-04", "monthIndex": 36, "label": "준공"}},
    [{"key": "construction_start", "planned_date": None, "month_index": 0, "source": {"sheet": "CF", "cell": "A5"}},
     {"key": "completion", "plannedDate": "2029-03-04", "monthIndex": 36}],
    {"construction_start": "M0", "completion": "M36"},
    None,
])
def test_schedule_preserves_captured_dates_month_zero_and_provenance_without_guessing(schedule):
    report = report_with_cf()
    report["cashFlowCalculationSnapshot"]["inputSnapshot"]["schedule"] = deepcopy(schedule)
    original = deepcopy(report["cashFlowCalculationSnapshot"])
    attach_timing_basis(report)
    assert report["sections"]["projectSchedule"]["저장된 일정 입력"] == schedule
    assert report["cashFlowCalculationSnapshot"] == original
    assert report["timingBasis"]["sectionRunIds"] == dict.fromkeys(SECTION_KEYS, original["calculationRunId"])
    verify_timing_basis(report)


def test_collections_and_provenance_are_exact_saved_results_and_independent_of_current_project():
    report = report_with_cf()
    captured = report["cashFlowCalculationSnapshot"]
    saved = [{"apartment": 0, "retail": 25.125}, {"apartment": 90.5, "retail": 0}]
    provenance = {"sourceMode": "imported_schedule", "reportEligible": False,
                  "sourceLocator": {"sourceName": "사업수지", "sheet": "CF", "cell": "L20"}}
    captured["resultSnapshot"]["salesCollectionSchedule"] = saved
    captured["resultSnapshot"]["scheduleProvenance"]["salesCollection"] = provenance
    captured["resultSnapshot"]["monthlyCashFlow"] = [{"monthIndex": 90, "cashIn": {"salesCollection": {"liveOnly": 99999}}}]
    report["sections"]["salesPaymentSchedule"] = {"liveOnly": 99999}
    original = deepcopy(captured)
    attach_timing_basis(report)
    section = report["sections"]["salesPaymentSchedule"]
    assert section["저장된 수금 결과"] == saved
    assert section["수금 근거"] == {"sourceMode": "imported_schedule", "reportEligible": False,
                                  "원본 위치": {"원본": "사업수지", "시트": "CF", "셀": "L20"}}
    assert section["수금 입력"] == captured["inputSnapshot"]["productMix"]
    assert "liveOnly" not in str(section)
    assert captured == original
    assert report["timingBasis"]["runInputHashes"] == {"cash_flow": content_hash(captured["inputSnapshot"])}
    assert report["timingBasis"]["runResultHashes"] == {"cash_flow": content_hash(captured["resultSnapshot"])}


def test_missing_saved_collections_are_not_rebuilt_from_monthly_rows_or_default_ratios():
    report = report_with_cf()
    result = report["cashFlowCalculationSnapshot"]["resultSnapshot"]
    result.pop("salesCollectionSchedule")
    assert result["monthlyCashFlow"]
    attach_timing_basis(report)
    section = report["sections"]["salesPaymentSchedule"]
    assert section["저장된 수금 결과"] is None
    assert "미확인" in section["상태"]
    verify_timing_basis(report)


@pytest.mark.parametrize("provenance,expected", [
    ({"sourceMode": "approved_workbook", "reportEligible": True}, "원본 기반"),
    ({"sourceMode": "asset_template_estimate", "reportEligible": False}, "추정 포함"),
    ({"sourceMode": "fallback", "reportEligible": True}, "추정 포함"),
    ({"sourceMode": "workbook_candidate", "reportEligible": False}, "추정 포함"),
    ({}, "추정 포함"),
])
def test_saved_collection_and_allocation_estimates_remain_explicitly_unverified(provenance, expected):
    report = report_with_cf()
    saved_provenance = report["cashFlowCalculationSnapshot"]["resultSnapshot"]["scheduleProvenance"]
    saved_provenance.update(salesCollection=deepcopy(provenance), collectionAllocation=deepcopy(provenance))
    attach_timing_basis(report)
    assert expected in report["sections"]["salesPaymentSchedule"]["상태"]
    assert expected in report["sections"]["financialStructureDiagram"]["배분 근거 상태"]
    assert report["sections"]["salesPaymentSchedule"]["수금 근거"] == provenance
    verify_timing_basis(report)


def test_structure_preserves_funding_pf_allocation_and_missing_or_explicit_parties():
    report = report_with_cf()
    run = report["cashFlowCalculationSnapshot"]
    source = run["inputSnapshot"]
    source["project"] = {"projectName": "저장된 사업", "borrower": None, "trustee": {"name": "저장된 신탁사"}}
    source["developer_info"] = {"name": "덮어쓰면 안 되는 시행사"}
    source["contractor_info"] = {"name": "저장된 시공사", "sourceCell": "B2"}
    source["pfStructure"] = [{"trancheId": "senior", "amount": 0, "interestRate": .058, "repaymentPriority": 1}]
    source["collectionAllocationRules"] = {"rules": [], "approvalStatus": "candidate", "sourceLocator": None}
    result = run["resultSnapshot"]["scheduleProvenance"]["collectionAllocation"]
    original = deepcopy(run)
    attach_timing_basis(report)
    diagram = report["sections"]["financialStructureDiagram"]
    parties = diagram["참여자 입력"]
    assert [p["저장된 참여자 입력"] for p in parties] == [None, {"name": "저장된 신탁사"}, source["contractor_info"], None]
    assert parties[0]["원본 위치"] == "미기록"
    assert parties[2]["원본 위치"] == {"셀": "B2"}
    assert parties[3]["원본 위치"] == "미기록"
    paths = report["timingBasis"]["sectionPaths"]
    assert paths["financialStructureDiagram"]["participants"]["borrower"] == "inputSnapshot.project.borrower"
    assert paths["financialStructureDiagram"]["participants"]["lenders"] is None
    assert "inputSnapshot." not in str({key: report["sections"][key] for key in SECTION_KEYS})
    assert "resultSnapshot." not in str({key: report["sections"][key] for key in SECTION_KEYS})
    assert diagram["조달 입력"] == source["sourcesAndUses"]["funding"]
    assert diagram["PF 구조 입력"] == source["pfStructure"]
    assert diagram["수금 배분 입력"] == {"rules": [], "approvalStatus": "candidate", "원본 위치": "미기록"}
    assert diagram["계산에 사용된 배분 근거"] == {
        **{key: value for key, value in result.items() if key != "sourceLocator"}, "원본 위치": "미기록"}
    assert run == original
    verify_timing_basis(report)


def test_partial_selected_report_clears_live_sections_and_legacy_report_is_untouched():
    report = ReportAgent().generate_structured_report({
        "schedule": {"completion": "2030-05-01"}, "developer_info": {"name": "live party"},
        "sales_schedule": {"contract_deposit": .1, "middle_payment": .6, "balance": .3},
    })
    legacy = deepcopy(report)
    attach_timing_basis(report)
    assert report == legacy and "timingBasis" not in report
    attach_calculation_bases(report, {"feasibility": feasibility_record()})
    attach_timing_basis(report)
    assert report["timingBasis"] == {"schemaVersion": VERSION, "sectionRunIds": dict.fromkeys(SECTION_KEYS),
                                     "runInputHashes": {}, "runResultHashes": {}, "sectionPaths": {}}
    assert all("미확인" in report["sections"][key]["상태"] for key in SECTION_KEYS)
    assert "live party" not in str({key: report["sections"][key] for key in SECTION_KEYS})
    verify_timing_basis(report)


@pytest.mark.parametrize("mutation", ["date", "collections", "party", "input_hash", "result_hash", "run_id", "version"])
def test_tampered_sections_or_binding_are_rejected(mutation):
    report = report_with_cf()
    attach_timing_basis(report)
    if mutation == "date": report["sections"]["projectSchedule"]["저장된 일정 입력"]["completion"] = "2030-01-01"
    elif mutation == "collections": report["sections"]["salesPaymentSchedule"]["저장된 수금 결과"][0]["apartment"] = 99999
    elif mutation == "party": report["sections"]["financialStructureDiagram"]["참여자 입력"][0]["저장된 참여자 입력"] = "forged borrower"
    elif mutation == "input_hash": report["timingBasis"]["runInputHashes"]["cash_flow"] = "f" * 64
    elif mutation == "result_hash": report["timingBasis"]["runResultHashes"]["cash_flow"] = "f" * 64
    elif mutation == "run_id": report["timingBasis"]["sectionRunIds"]["projectSchedule"] = "foreign run"
    else: report["timingBasis"]["schemaVersion"] = "future version"
    with pytest.raises(ValueError): verify_timing_basis(report)


def test_timing_connection_updates_human_scope_and_keeps_paths_only_in_json_binding():
    report = report_with_cf()
    attach_financing_basis(report)
    attach_timing_basis(report)
    assert report["sections"]["summaryCashFlow"]["assumption"] == TIMING_CF_NOTE
    assert report["calculationSources"][0]["적용 범위"] == CF_SCOPE
    assert report["calculationSources"][0]["검토 한계"] == TIMING_CF_NOTE
    paths = report["timingBasis"]["sectionPaths"]
    assert paths["projectSchedule"]["schedule"] == "inputSnapshot.schedule"
    assert paths["salesPaymentSchedule"]["collections"] == "resultSnapshot.salesCollectionSchedule"
    assert report["sections"]["projectSchedule"]["일정 근거"]["원본 위치"] == "미기록"
    assert report["sections"]["salesPaymentSchedule"]["자료 구분"] == "선택 계산의 분양수금 결과"
    verify_timing_basis(report)
    report["sections"]["summaryCashFlow"]["assumption"] = "Old limited scope"
    with pytest.raises(ValueError): verify_timing_basis(report)


def test_business_source_labels_use_captured_sheet_cells_without_displaying_internal_paths():
    report = report_with_cf()
    run = report["cashFlowCalculationSnapshot"]
    run["inputSnapshot"]["sourceMetadata"] = {
        "sourceName": "검토 사업수지.xlsx", "sourceLocator": {"sheet": "일정", "cell": "B20", "fieldPath": "inputSnapshot.schedule"}}
    run["resultSnapshot"]["scheduleProvenance"]["salesCollection"]["sourceLocator"] = {
        "sourceName": "검토 사업수지.xlsx", "sheet": "수금", "cell": "C30:F30", "fieldPath": "resultSnapshot.salesCollectionSchedule"}
    attach_timing_basis(report)
    assert report["sections"]["projectSchedule"]["일정 근거"]["원본 위치"] == {
        "원본": "검토 사업수지.xlsx", "시트": "일정", "셀": "B20"}
    assert report["sections"]["salesPaymentSchedule"]["원본 위치"] == {
        "원본": "검토 사업수지.xlsx", "시트": "수금", "셀": "C30:F30"}
    assert run["inputSnapshot"]["sourceMetadata"]["sourceLocator"]["fieldPath"] == "inputSnapshot.schedule"
    verify_timing_basis(report)


@pytest.mark.parametrize("file_key", ["sourceName", "sourceDocument"])
def test_human_exports_preserve_provenance_caveats_and_files_without_internal_json_paths(file_key):
    report = report_with_cf()
    run = report["cashFlowCalculationSnapshot"]
    sales_provenance = {
        "sourceMode": "asset_template_estimate", "approvalStatus": "candidate", "reportEligible": False,
        "warnings": ["분양 수금 원본이 없어 추정되었습니다.", "sales_collection_schedule_unapproved"],
        "sourceLocator": {file_key: "검토 사업수지.xlsx", "sheet": "수금", "cell": "L20", "range": "L20:O20",
                          "fieldPath": "resultSnapshot.salesCollectionSchedule", "sourcePath": "inputSnapshot.productMix"},
    }
    allocation = {
        "sourceMode": "benchmark_estimate", "approvalStatus": "unapproved", "reportEligible": False,
        "warnings": ["수금 배분 약정이 확인되지 않았습니다."],
        "rules": [{"contractAndInterimToOperating": .75, "sourceLocator": {
            file_key: "배분 검토.xlsx", "sheet": "분양수금", "range": "D12:G12", "fieldPath": "inputSnapshot.collectionAllocationRules.rules[0]"}}],
        "sourceLocator": {file_key: "배분 검토.xlsx", "sheet": "분양수금", "cell": "D12", "fieldPath": "resultSnapshot.scheduleProvenance.collectionAllocation"},
    }
    run["resultSnapshot"]["scheduleProvenance"].update(salesCollection=deepcopy(sales_provenance), collectionAllocation=deepcopy(allocation))
    run["inputSnapshot"]["collectionAllocationRules"] = deepcopy(allocation)
    original = deepcopy(run)
    attach_timing_basis(report)
    sections = {key: report["sections"][key] for key in SECTION_KEYS}
    sales = sections["salesPaymentSchedule"]["수금 근거"]
    assert sales["sourceMode"] == "asset_template_estimate" and sales["reportEligible"] is False
    assert sales["warnings"] == sales_provenance["warnings"]
    assert sales["원본 위치"] == {"원본": "검토 사업수지.xlsx", "시트": "수금", "셀": "L20", "범위": "L20:O20"}
    applied = sections["financialStructureDiagram"]["계산에 사용된 배분 근거"]
    assert applied["warnings"] == allocation["warnings"] and applied["approvalStatus"] == "unapproved"
    assert applied["rules"][0]["contractAndInterimToOperating"] == .75
    assert applied["rules"][0]["원본 위치"] == {"원본": "배분 검토.xlsx", "시트": "분양수금", "범위": "D12:G12"}
    assert run == original
    assert report["timingBasis"]["runInputHashes"]["cash_flow"] == content_hash(original["inputSnapshot"])
    assert report["timingBasis"]["runResultHashes"]["cash_flow"] == content_hash(original["resultSnapshot"])
    agent = ReportExportAgent()
    markdown = agent.export({"report": report}, "markdown")["content"]
    docx = agent._render_docx({"report": report})
    with ZipFile(BytesIO(docx)) as archive:
        docx_text = "".join(ElementTree.fromstring(archive.read("word/document.xml")).itertext())
    for text in (markdown, docx_text):
        assert "resultSnapshot." not in text and "inputSnapshot." not in text and "fieldPath" not in text and "sourcePath" not in text
        assert all(label in text for label in ("검토 사업수지.xlsx", "수금", "L20", "L20:O20", "배분 검토.xlsx", "D12:G12"))
        assert all(warning in text for warning in sales_provenance["warnings"] + allocation["warnings"])
    json_export = agent.export({"report": report}, "json")["content"]
    assert "resultSnapshot.salesCollectionSchedule" in json_export
    verify_timing_basis(report)


def test_selected_timing_sections_save_reopen_and_export_from_frozen_run(selected_run, monkeypatch):
    client, pid, run, selection = selected_run
    projects.project_repository().update(pid, {
        "schedule": {"completion": "2099-01-01"}, "developer_info": {"name": "current-only borrower"},
        "sales_schedule": {"contract_deposit": .99, "middle_payment": .005, "balance": .005},
    })
    created = client.post(f"/projects/{pid}/reports/generate", json={"cashFlowBasis": selection})
    assert created.status_code == 200, created.text
    saved = created.json()
    report = saved["report"]
    sections = {key: report["sections"][key] for key in SECTION_KEYS}
    assert report["timingBasis"]["sectionRunIds"] == dict.fromkeys(SECTION_KEYS, run["calculationRunId"])
    assert saved["metadata"]["timing_basis_hash"] == content_hash(report["timingBasis"])
    assert sections["projectSchedule"]["저장된 일정 입력"] == run["inputSnapshot"]["schedule"]
    assert sections["salesPaymentSchedule"]["저장된 수금 결과"] == run["resultSnapshot"]["salesCollectionSchedule"]
    assert "2099-01-01" not in str(sections) and "current-only borrower" not in str(sections)
    monkeypatch.setattr(projects, "list_calculation_run_records", lambda *args, **kwargs: pytest.fail("Reopen/export must not read live runs"))
    projects.project_repository().update(pid, {"schedule": {"completion": "2098-01-01"}})
    endpoint = f"/projects/{pid}/reports/{saved['report_id']}"
    reopened = client.get(endpoint)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["report"] == report
    for fmt in ("json", "markdown", "html", "pdf", "docx"):
        exported = client.post(endpoint + "/exports", json={"format": fmt})
        assert exported.status_code == 200, exported.text
        downloaded = client.get(endpoint + f"/exports/{exported.json()['export_id']}/download")
        assert downloaded.status_code == 200
        if fmt == "json":
            assert {key: downloaded.json()["report"]["sections"][key] for key in SECTION_KEYS} == sections


@pytest.mark.parametrize("mutation", ["schedule", "collections", "party", "basis", "removed", "seal"])
def test_timing_mutation_rejected_on_reopen_and_export(selected_run, mutation):
    client, pid, _, selection = selected_run
    created = client.post(f"/projects/{pid}/reports/generate", json={"cashFlowBasis": selection})
    assert created.status_code == 200, created.text
    saved = created.json()
    stored = projects.report_repository().get(pid, saved["report_id"])
    if mutation == "schedule": stored["report"]["sections"]["projectSchedule"]["저장된 일정 입력"]["completion"] = "2099-01-01"
    elif mutation == "collections": stored["report"]["sections"]["salesPaymentSchedule"]["저장된 수금 결과"][0]["apartment"] = 99999
    elif mutation == "party": stored["report"]["sections"]["financialStructureDiagram"]["참여자 입력"][0]["저장된 참여자 입력"] = "unverified borrower"
    elif mutation == "basis": stored["report"]["timingBasis"]["runResultHashes"]["cash_flow"] = "f" * 64
    elif mutation == "removed": del stored["report"]["timingBasis"]
    else: stored["metadata"]["timing_basis_hash"] = "f" * 64
    endpoint = f"/projects/{pid}/reports/{saved['report_id']}"
    assert client.get(endpoint).status_code == 409
    assert client.post(endpoint + "/exports", json={"format": "docx"}).status_code == 409
