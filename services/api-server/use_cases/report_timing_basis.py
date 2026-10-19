"""Freeze schedule, collections and financing structure from the selected CF run."""
from copy import deepcopy

from use_cases.cash_flow_comparison import content_hash
from use_cases.report_financing_basis import TIMING_CF_NOTE


VERSION = "report-timing-inputs-v1"
NOTE = (
    "선택한 현금흐름 계산에 저장된 입력·결과입니다. 일정과 분양수금은 분석용 계획이며 실제 이행·입금을 뜻하지 않습니다. "
    "참여자·배분조건이 없는 경우 미확인으로 유지합니다. 실제 약정·책임준공·담보권과 최종 승인은 별도 검토합니다."
)
SECTION_KEYS = ("projectSchedule", "salesPaymentSchedule", "financialStructureDiagram")
CF_SCOPE = "5. 자금조달 · 6. 자금사용 · 8. 사업추진 일정 · 9. 금융구조도 · 10. 분양대금 납입 일정 · 12. 요약 현금흐름표"
_PARTIES = (("borrower", "차주", "developer_info"), ("trustee", "신탁사", "trustee"),
            ("contractor", "시공사", "contractor_info"), ("lenders", "대주단", "financial_institution"))


def _object(value):
    return value if isinstance(value, dict) else {}


def _participant(source, project, key, legacy):
    """Read only explicitly captured party fields; role labels are never names."""
    if key in project:
        return deepcopy(project[key]), f"inputSnapshot.project.{key}"
    if key in source:
        return deepcopy(source[key]), f"inputSnapshot.{key}"
    if legacy in project:
        return deepcopy(project[legacy]), f"inputSnapshot.project.{legacy}"
    if legacy in source:
        return deepcopy(source[legacy]), f"inputSnapshot.{legacy}"
    return None, None


def _business_locator(value):
    value = _object(value)
    locator = _object(value.get("sourceLocator"))
    result = {}
    for label, keys in (("원본", ("sourceName", "sourceDocument")), ("시트", ("sheet", "sourceSheet", "sheetName")),
                        ("셀", ("cell", "sourceCell", "cellRef")), ("범위", ("range", "sourceRange", "cellRange"))):
        found = next((item[key] for item in (locator, value) for key in keys
                      if isinstance(item.get(key), str) and item[key].strip()), None)
        if found is not None:
            result[label] = found
    return result or "미기록"


def _business_provenance(value):
    """Keep saved caveats and rules, showing document locations rather than JSON paths."""
    if isinstance(value, list):
        return [_business_provenance(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {}
    for key, item in value.items():
        if key == "sourceLocator":
            result["원본 위치"] = _business_locator({"sourceLocator": item})
        elif key not in {"fieldPath", "field_path", "sourcePath", "source_path", "jsonPath", "json_path"}:
            result[key] = _business_provenance(item)
    return result


def _source_status(value):
    provenance = _object(value)
    mode = str(provenance.get("sourceMode") or "").casefold()
    if provenance.get("reportEligible") is True and not any(token in mode for token in ("estimate", "fallback", "template")):
        return "저장된 원본 기반 결과 — 실제 이행·계약은 별도 확인"
    return "분석용 결과 — 근거 미확인·추정 포함"


def timing_sections(report: dict) -> tuple[dict, dict]:
    """Derive display sections without resolving dates or rerunning collection rules."""
    sections = {key: {"상태": "미확인 — 현금흐름 계산 미선택", "검토 한계": NOTE}
                for key in SECTION_KEYS}
    basis = {"schemaVersion": VERSION, "sectionRunIds": {key: None for key in SECTION_KEYS},
             "runInputHashes": {}, "runResultHashes": {}, "sectionPaths": {}}
    run = report.get("cashFlowCalculationSnapshot")
    if not run:
        return sections, basis

    source, result = run["inputSnapshot"], run["resultSnapshot"]
    provenance = _object(result.get("scheduleProvenance"))
    common = {"계산 기록": run["calculationRunId"], "검토 한계": NOTE}
    schedule = source.get("schedule")
    sections["projectSchedule"] = {
        **common, "상태": "저장된 일정 입력" if schedule else "미확인 — 저장 일정 없음",
        "저장된 일정 입력": deepcopy(schedule),
        "일정 근거": {"구분": "선택 계산의 일정 입력", "원본 위치": _business_locator(source.get("sourceMetadata"))},
        "일정 해석": "날짜와 월차는 저장된 값을 그대로 표시합니다. monthIndex 0은 첫 월차이며 날짜 미입력은 미확인입니다. 기준일·일정·기간을 새로 추정하지 않습니다.",
    }
    collections = result.get("salesCollectionSchedule")
    sections["salesPaymentSchedule"] = {
        **common, "상태": _source_status(provenance.get("salesCollection")) if isinstance(collections, list) and collections else "미확인 — 저장된 수금 결과 없음",
        "저장된 수금 결과": deepcopy(collections),
        "수금 입력": deepcopy(source.get("productMix")),
        "수금 근거": _business_provenance(provenance.get("salesCollection")),
        "자료 구분": "선택 계산의 분양수금 결과",
        "원본 위치": _business_locator(provenance.get("salesCollection")),
        "월차 기준": "저장 결과 목록의 첫 행은 M0이며 이후 행은 M1, M2 순서입니다. 누락된 결과는 월별 현금흐름이나 기본 수금비율로 다시 만들지 않습니다.",
    }
    project = _object(source.get("project"))
    participants = []
    participant_paths = {}
    for key, role, legacy in _PARTIES:
        party, locator = _participant(source, project, key, legacy)
        participants.append({"역할": role, "저장된 참여자 입력": party, "원본 위치": _business_locator(party)})
        participant_paths[key] = locator
    sections["financialStructureDiagram"] = {
        **common, "상태": "저장된 자금조달·배분 구조 입력",
        "참여자 입력": participants,
        "조달 입력": deepcopy(_object(source.get("sourcesAndUses")).get("funding")),
        "PF 구조 입력": deepcopy(source.get("pfStructure")),
        "수금 배분 입력": _business_provenance(source.get("collectionAllocationRules", source.get("collection_allocation_rules"))),
        "계산에 사용된 배분 근거": _business_provenance(provenance.get("collectionAllocation")),
        "배분 근거 상태": _source_status(provenance.get("collectionAllocation")),
        "구조 해석": "저장된 조달·트랜치·수금 배분을 표시합니다. 미확인 참여자나 계약상 송금 관계·담보권·신용보강을 추가하지 않습니다.",
    }
    basis["sectionRunIds"] = {key: run["calculationRunId"] for key in SECTION_KEYS}
    basis["runInputHashes"]["cash_flow"] = content_hash(source)
    basis["runResultHashes"]["cash_flow"] = content_hash(result)
    basis["sectionPaths"] = {
        "projectSchedule": {"schedule": "inputSnapshot.schedule"},
        "salesPaymentSchedule": {"collections": "resultSnapshot.salesCollectionSchedule",
                                 "productMix": "inputSnapshot.productMix", "provenance": "resultSnapshot.scheduleProvenance.salesCollection"},
        "financialStructureDiagram": {"funding": "inputSnapshot.sourcesAndUses.funding", "pfStructure": "inputSnapshot.pfStructure",
                                      "allocation": "inputSnapshot.collectionAllocationRules" if "collectionAllocationRules" in source else "inputSnapshot.collection_allocation_rules",
                                      "provenance": "resultSnapshot.scheduleProvenance.collectionAllocation", "participants": participant_paths},
    }
    return sections, basis


def attach_timing_basis(report: dict) -> None:
    if report.get("cashFlowCalculationSnapshot") or report.get("calculationSnapshots"):
        sections, basis = timing_sections(report)
        report["sections"].update(sections)
        report["timingBasis"] = basis
        if report.get("cashFlowCalculationSnapshot"):
            report["sections"]["summaryCashFlow"]["assumption"] = TIMING_CF_NOTE
            for source in report.get("calculationSources", []):
                if str(source.get("적용 범위") or "").startswith(("5.", "12.")):
                    source.update({"적용 범위": CF_SCOPE, "검토 한계": TIMING_CF_NOTE})


def verify_timing_basis(report: dict) -> None:
    sections, basis = timing_sections(report)
    if report["timingBasis"] != basis or basis["schemaVersion"] != VERSION:
        raise ValueError("Timing input basis differs from captured runs")
    if any(report["sections"].get(key) != value for key, value in sections.items()):
        raise ValueError("Timing report sections differ from captured inputs and results")
    if report.get("cashFlowCalculationSnapshot") and report["sections"]["summaryCashFlow"].get("assumption") != TIMING_CF_NOTE:
        raise ValueError("Cash-flow timing scope differs")
