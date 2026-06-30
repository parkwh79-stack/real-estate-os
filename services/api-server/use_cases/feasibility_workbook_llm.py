from __future__ import annotations

"""LLM-assisted feasibility workbook extraction.

The deterministic importer (`feasibility_workbook_importer`) only reads ONE fixed
template (sheets 사업수지/입력/분양/2.현금흐름). Real-world 사업수지 workbooks vary by
developer, so this module dumps an arbitrary workbook's cells to text and asks the
configured LLM to extract the canonical financial figures **with source-cell
evidence and the detected unit**. It NEVER persists on its own — it returns a
reviewable draft so a human confirms/corrects (unit mis-reads are possible) before
the values are applied to the project via the audited apply endpoint.
"""

from typing import Any, Dict, List, Optional

import openpyxl

from agents.ai_connection import AIConnection, resolve_ai_context
from agents.agent_policy import system_prompt_for

EXTRACT_FIELDS = [
    "total_revenue",
    "total_cost",
    "land_cost",
    "construction_cost",
    "finance_cost",
    "other_cost",
    "development_profit",
    "pf_amount",
]

_PROMPT_HEADER = (
    "다음은 한국 부동산 PF '사업수지' 엑셀의 셀 덤프입니다 (형식: 시트!셀=값).\n"
    "이 워크북에서 아래 항목을 추출하세요. 단위 헤더(예: '금액(천원)', '백만원', '억원')를 찾아\n"
    "반드시 **원(KRW)** 으로 환산하세요(천원=×1000, 백만원=×1,000,000, 억원=×100,000,000).\n"
    "각 항목마다 출처 셀(시트!셀)과 감지한 단위, 원본값을 함께 주세요. 합계 셀을 우선하세요. 못 찾으면 null.\n"
    "항목: total_revenue(총매출/매출액 합계), total_cost(총사업비 합계), land_cost(토지비 소계),\n"
    "construction_cost(공사비/건축비), finance_cost(금융비용), other_cost(기타사업비),\n"
    "development_profit(사업이익), pf_amount(PF 대출 총액).\n"
    'JSON만 출력: {"fields": {"<field>": {"value_krw": <int|null>, "source": "<시트!셀>", '
    '"unit": "<천원|백만원|억원|원>", "raw": <원본값|null>}, ...}}\n\n셀 덤프:\n'
)


def dump_workbook_cells(path: str, max_chars: int = 24000) -> str:
    """Compact `Sheet!Cell=value` dump of non-empty cells (data_only), capped."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    lines: List[str] = []
    try:
        for name in workbook.sheetnames:
            sheet = workbook[name]
            lines.append(f"\n### SHEET: {name}")
            for row in sheet.iter_rows():
                for cell in row:
                    value = cell.value
                    if value is None or value == "":
                        continue
                    if isinstance(value, float):
                        value = round(value, 4)
                    lines.append(f"{name}!{cell.coordinate}={value}")
                    if sum(len(x) for x in lines) > max_chars:
                        lines.append("...(truncated)")
                        return "\n".join(lines)[:max_chars]
    finally:
        workbook.close()
    return "\n".join(lines)[:max_chars]


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def extract_feasibility_fields_via_llm(
    path: str,
    ai_context: Dict[str, Any] | None = None,
    *,
    ai: AIConnection | None = None,
) -> Dict[str, Any]:
    """Return a reviewable extraction draft (fields + evidence + candidate input).

    Non-fabricating: only fields the LLM grounds in a source cell are surfaced;
    the result is a DRAFT for human review, never auto-applied.
    """
    context = resolve_ai_context(ai_context)
    dump = dump_workbook_cells(path)
    client = ai or AIConnection()
    result = client.extract_json(
        _PROMPT_HEADER + dump,
        system=system_prompt_for(
            "feasibility_workbook",
            base="You are a precise financial data extractor for Korean real-estate PF. Output only JSON.",
        ),
        ai_context=context,
    )
    if result.data is None:
        return {
            "schemaVersion": "realytics-feasibility-llm-extract-v1",
            "parsingSource": "llm_unavailable",
            "error": result.error,
            "fields": {},
            "feasibilityInputCandidate": {},
        }

    raw_fields = result.data.get("fields") if isinstance(result.data, dict) else None
    fields: Dict[str, Any] = {}
    candidate: Dict[str, Any] = {}
    if isinstance(raw_fields, dict):
        for key in EXTRACT_FIELDS:
            entry = raw_fields.get(key)
            if not isinstance(entry, dict):
                continue
            value_krw = _coerce_int(entry.get("value_krw"))
            fields[key] = {
                "value_krw": value_krw,
                "source": entry.get("source"),
                "unit": entry.get("unit"),
                "raw": entry.get("raw"),
            }
            if value_krw is not None:
                candidate[key] = value_krw

    return {
        "schemaVersion": "realytics-feasibility-llm-extract-v1",
        "parsingSource": "llm",
        "aiConnection": result.meta,
        "fields": fields,
        "feasibilityInputCandidate": candidate,
        "reviewRequired": True,
        "note": "LLM 추출 결과(증거 포함)입니다. 단위 오인 가능 — 검토·수정 후 apply 하세요.",
    }


FEASIBILITY_INPUT_KEYS = {
    "total_revenue",
    "total_cost",
    "land_cost",
    "construction_cost",
    "finance_cost",
    "other_cost",
}


def apply_feasibility_input_use_case(
    project_repository,
    project_id: str,
    feasibility_input: Dict[str, Any],
    *,
    applied_by: str,
    source_document: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Persist a reviewer-confirmed feasibility extraction onto the project (human-gated).

    Revenue/cost fields go to ``feasibility_input`` (consumed by the cash-flow engine
    and the feasibility page); ``pf_amount``/``development_profit`` are routed to
    ``expected_results`` so the finance/report pages show real PF/profit too.
    """
    numeric = {
        key: feasibility_input[key]
        for key in feasibility_input
        if isinstance(feasibility_input.get(key), (int, float)) and feasibility_input.get(key) is not None
    }
    if not numeric:
        raise ValueError("적용할 수치가 없습니다.")
    existing = project_repository.get(project_id)
    if not existing:
        raise LookupError("Project not found")

    fi_update = {k: v for k, v in numeric.items() if k in FEASIBILITY_INPUT_KEYS}

    expected = dict(existing.get("expected_results") or {})
    if "pf_amount" in numeric:
        expected["total_pf_amount"] = numeric["pf_amount"]
    if "development_profit" in numeric:
        expected["development_profit"] = numeric["development_profit"]
        revenue = fi_update.get("total_revenue") or (existing.get("feasibility_input") or {}).get("total_revenue")
        if revenue:
            expected["profit_margin"] = round(numeric["development_profit"] / float(revenue), 4)

    history = list(((existing.get("_meta") or {}).get("appliedFeasibilityInputs") or []))
    history.append({"appliedBy": applied_by, "fields": list(numeric.keys()), "sourceDocument": source_document or {}})

    update_payload: Dict[str, Any] = {
        "feasibility_input": {**(existing.get("feasibility_input") or {}), **fi_update},
        "_meta": {"appliedFeasibilityInputs": history, "feasibilityInputSource": "llm_workbook_review"},
    }
    if expected != (existing.get("expected_results") or {}):
        update_payload["expected_results"] = expected

    updated = project_repository.update(project_id, update_payload)
    return {
        "schemaVersion": "realytics-feasibility-input-apply-v1",
        "projectId": project_id,
        "appliedFields": numeric,
        "feasibility_input": (updated or {}).get("feasibility_input"),
        "expected_results": (updated or {}).get("expected_results"),
        "project": updated,
    }
