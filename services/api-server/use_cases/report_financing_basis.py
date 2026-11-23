"""Bind financing sections to captured inputs without running financial engines."""
from copy import deepcopy
import math

from use_cases.cash_flow_comparison import content_hash
from use_cases.report_calculation_basis import calculation_sections, CONNECTED_CALCULATION_NOTE


VERSION = "report-financing-inputs-v1"
NOTE = (
    "선택한 계산에 저장된 분석용 입력입니다. 예산·약정 한도는 실제 입출금이나 대출 약정 확인과 구분합니다. "
    "누락값은 미확인으로 유지하며 나머지 절의 계산 기준과 최종 승인은 별도 검토합니다."
)
CF_NOTE = "요약 현금흐름표·자금조달·자금사용 절은 아래 저장 계산을 사용합니다. 다른 절은 각 절에 표시한 기준을 확인해 주세요. " + NOTE
TIMING_CF_NOTE = "요약 현금흐름표·자금조달·자금사용·사업 일정·분양수금·금융구조 절은 아래 저장 현금흐름 계산을 사용합니다. 다른 절은 별도 선택 계산 또는 생성 당시 프로젝트 자료를 사용합니다. " + NOTE
FUNDING = (("equity", "자기자본"), ("bridgeLoan", "브리지 대출"), ("pfLoan", "PF 대출"))
REVENUE = (("totalSalesRevenue", "총 분양수입"), ("leaseDeposit", "임대보증금"),
           ("forwardSaleProceeds", "선매각 수입"), ("refinancingProceeds", "리파이낸싱 수입"),
           ("otherRevenue", "기타 수입"))
COSTS = (("landCost", "토지비"), ("directConstructionCost", "직접 공사비"),
         ("financialCost", "금융비용 예산"), ("otherCost", "기타 비용"))
SECTION_KEYS = ("financialTerms", "fundingStructure", "useOfFunds")


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _object(value):
    return value if isinstance(value, dict) else {}


def _items(source, fields):
    return [{"항목": label, "금액": _number(source.get(key))} for key, label in fields]


def financing_sections(report: dict) -> tuple[dict, dict]:
    cf = report.get("cashFlowCalculationSnapshot")
    pf = (report.get("calculationSnapshots") or {}).get("pf")
    sections = {key: {"상태": "미확인 — 현금흐름 계산 미선택", "검토 한계": NOTE}
                for key in ("fundingStructure", "useOfFunds")}
    sections["financialTerms"] = {"상태": "미확인 — PF 계산 미선택", "검토 한계": NOTE}
    refs = {key: None for key in SECTION_KEYS}
    hashes = {}
    if cf:
        source = cf["inputSnapshot"]
        uses = _object(source.get("sourcesAndUses"))
        unit = source.get("unit", "KRW")
        currency = source.get("currency", "KRW")
        common = {"계산 기록": cf["calculationRunId"], "검토 한계": NOTE,
                  "통화": currency if isinstance(currency, str) else "미확인",
                  "단위": unit if isinstance(unit, str) else "미확인",
                  "단위 근거": "저장 입력" if "unit" in source else "계산 API 기본 원 단위"}
        sections["fundingStructure"] = {**common,
            "조달 입력": _items(_object(uses.get("funding")), FUNDING),
            "사업 수입 입력": _items(_object(uses.get("revenue")), REVENUE),
            "집계 범위": "조달과 사업 수입을 각각 표시합니다. 매출과 차입을 합산한 총조달액이나 월별 집행액으로 해석하지 않습니다."}
        sections["useOfFunds"] = {**common,
            "총사업비 예산": _number(_object(uses.get("cost")).get("totalProjectCost")),
            "사용 예산 입력": _items(_object(uses.get("cost")), COSTS),
            "집계 범위": "총사업비와 세부 비용은 별도 입력입니다. 공사비 부대항목을 직접 공사비에 합산하거나 누락된 비용을 역산하지 않습니다."}
        refs.update(fundingStructure=cf["calculationRunId"], useOfFunds=cf["calculationRunId"])
        hashes["cash_flow"] = content_hash(source)
    if pf:
        source = pf["inputSnapshot"]
        tranches = source.get("tranches")
        terms = [{"명칭": t.get("tranche", t.get("name")) or "미기록",
                  "금액": _number(t.get("amount")),
                  "금리(소수)": _number(t.get("interest_rate", t.get("rate"))),
                  "수수료율(소수)": _number(t.get("fee_rate")),
                  "상환순위 입력": deepcopy(t.get("priority"))}
                 for t in tranches if isinstance(t, dict)] if isinstance(tranches, list) else []
        sections["financialTerms"] = {"계산 기록": pf["calculationRunId"], "검토 한계": NOTE,
            "통화": source.get("currency", "KRW"), "단위": source.get("unit", "KRW"),
            "단위 근거": "저장 입력" if "unit" in source else "계산 API 기본 원 단위", "금리 단위": "0.05는 5%입니다.",
            "상태": "저장된 PF 조건 입력" if terms else "미확인 — 저장된 트랜치 조건 없음",
            "트랜치 조건": terms,
            "기타 우선비용 입력": _number(source.get("other_priority_costs")),
            "조건 범위": "PF 엔진의 저장 입력을 표시합니다. 실제 계약의 변동금리·만기·약정 수수료와 총조달비용의 검증은 별도입니다."}
        refs["financialTerms"] = pf["calculationRunId"]
        hashes["pf"] = content_hash(source)
    return sections, {"schemaVersion": VERSION, "sectionRunIds": refs, "runInputHashes": hashes}


def attach_financing_basis(report: dict) -> None:
    if report.get("cashFlowCalculationSnapshot") or report.get("calculationSnapshots"):
        sections, basis = financing_sections(report)
        report["sections"].update(sections)
        report["financingBasis"] = basis
        if report.get("calculationSnapshots"):
            report["sections"].update(calculation_sections(report["calculationSnapshots"], note=CONNECTED_CALCULATION_NOTE))
        if report.get("cashFlowCalculationSnapshot"):
            report["sections"]["summaryCashFlow"]["assumption"] = CF_NOTE
        for source in report.get("calculationSources", []):
            if source.get("적용 범위") == "12. 요약 현금흐름표":
                source.update({"적용 범위": "5. 자금조달 · 6. 자금사용 · 12. 요약 현금흐름표", "검토 한계": CF_NOTE})
            elif "적용 항목" in source:
                source["검토 한계"] = CONNECTED_CALCULATION_NOTE


def verify_financing_basis(report: dict) -> None:
    sections, basis = financing_sections(report)
    if report["financingBasis"] != basis or basis["schemaVersion"] != VERSION:
        raise ValueError("Financing input basis differs from captured runs")
    if any(report["sections"].get(key) != value for key, value in sections.items()):
        raise ValueError("Financing report sections differ from captured inputs")
    note = TIMING_CF_NOTE if "timingBasis" in report else CF_NOTE
    if report.get("cashFlowCalculationSnapshot") and report["sections"]["summaryCashFlow"].get("assumption") != note:
        raise ValueError("Cash-flow financing scope differs")
