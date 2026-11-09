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

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

import openpyxl

from agents.ai_connection import AIConnection, resolve_ai_context
from agents.agent_policy import system_prompt_for
from core.text_chunking import chunk_text_preserving_context
from use_cases.feasibility_review_terms import TERM_GROUPS, patch_terms, reviewed_terms, validate_terms

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


WORKBOOK_EXTRACTION_CHUNK_CHARS = 24_000


def dump_workbook_cells(path: str) -> str:
    """Return every non-empty cell as compact ``Sheet!Cell=value`` text."""
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
    finally:
        workbook.close()
    return "\n".join(lines)


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
    chunk_chars: int = WORKBOOK_EXTRACTION_CHUNK_CHARS,
) -> Dict[str, Any]:
    """Return a reviewable extraction draft (fields + evidence + candidate input).

    Non-fabricating: only fields the LLM grounds in a source cell are surfaced;
    the result is a DRAFT for human review, never auto-applied.
    """
    context = resolve_ai_context(ai_context)
    dump = dump_workbook_cells(path)
    chunks = chunk_text_preserving_context(
        dump,
        max_chars=chunk_chars,
        is_context_line=lambda line: line.startswith("### SHEET:"),
    )
    client = ai or AIConnection()
    system = system_prompt_for(
        "feasibility_workbook",
        base="You are a precise financial data extractor for Korean real-estate PF. Output only JSON.",
    )
    successful: list[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
    failed_chunks: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        result = client.extract_json(
            _PROMPT_HEADER + chunk,
            system=system,
            ai_context=context,
        )
        if isinstance(result.data, Mapping):
            successful.append((index, result.data, result.meta or {}))
        else:
            failed_chunks.append(
                {
                    "chunkIndex": index,
                    "error": result.error or "LLM extraction unavailable",
                }
            )
    if not successful:
        return {
            "schemaVersion": "realytics-feasibility-llm-extract-v1",
            "parsingSource": "llm_unavailable",
            "error": "; ".join(item["error"] for item in failed_chunks),
            "fields": {},
            "feasibilityInputCandidate": {},
            "sourceWorkbookTextSha256": _sha256_text(dump),
            "chunkCount": len(chunks),
            "completedChunkCount": 0,
            "inputCoverage": "failed",
            "analysisOnly": True,
            "reportEligible": False,
        }

    fields, candidate, source_conflicts = _merge_workbook_chunk_fields(successful)
    extraction_empty = not candidate and not source_conflicts
    complete = not failed_chunks and not source_conflicts and not extraction_empty

    return {
        "schemaVersion": "realytics-feasibility-llm-extract-v1",
        "parsingSource": "llm" if len(chunks) == 1 else "llm_chunked",
        "aiConnection": successful[0][2],
        "fields": fields,
        "feasibilityInputCandidate": candidate,
        "sourceConflicts": source_conflicts,
        "sourceWorkbookTextSha256": _sha256_text(dump),
        "chunkCount": len(chunks),
        "completedChunkCount": len(successful),
        "failedChunks": failed_chunks,
        "extractionEmpty": extraction_empty,
        "inputCoverage": "complete" if not failed_chunks else "partial",
        "analysisOnly": not complete,
        "reportEligible": complete,
        "reviewRequired": True,
        "note": "LLM 추출 결과(증거 포함)입니다. 단위 오인 가능 — 검토·수정 후 apply 하세요.",
    }


def _merge_workbook_chunk_fields(
    chunks: List[tuple[int, Mapping[str, Any], Mapping[str, Any]]],
) -> tuple[Dict[str, Any], Dict[str, int], List[Dict[str, Any]]]:
    fields: Dict[str, Any] = {}
    candidate: Dict[str, int] = {}
    conflicts: List[Dict[str, Any]] = []
    for key in EXTRACT_FIELDS:
        entries: list[dict[str, Any]] = []
        for chunk_index, payload, _meta in chunks:
            raw_fields = payload.get("fields")
            raw_entry = raw_fields.get(key) if isinstance(raw_fields, Mapping) else None
            if not isinstance(raw_entry, Mapping):
                continue
            value_krw = _coerce_int(raw_entry.get("value_krw"))
            if value_krw is None:
                continue
            entry = {
                "value_krw": value_krw,
                "source": raw_entry.get("source"),
                "unit": raw_entry.get("unit"),
                "raw": raw_entry.get("raw"),
                "chunkIndex": chunk_index,
            }
            signature = _stable_json(
                {
                    "value_krw": entry["value_krw"],
                    "source": entry["source"],
                    "unit": entry["unit"],
                    "raw": entry["raw"],
                }
            )
            if all(item["_signature"] != signature for item in entries):
                entries.append({**entry, "_signature": signature})
        unique_values = {entry["value_krw"] for entry in entries}
        if len(unique_values) > 1:
            public_entries = [
                {
                    item_key: item_value
                    for item_key, item_value in entry.items()
                    if item_key != "_signature"
                }
                for entry in entries
            ]
            fields[key] = {
                "value_krw": None,
                "status": "conflict",
                "candidates": public_entries,
            }
            conflicts.append(
                {
                    "fieldKey": key,
                    "status": "open",
                    "candidates": public_entries,
                }
            )
            continue
        if not entries:
            continue
        chosen = entries[0]
        fields[key] = {
            item_key: item_value
            for item_key, item_value in chosen.items()
            if item_key not in {"_signature", "chunkIndex"}
        }
        fields[key]["evidence"] = [
            {
                "source": entry.get("source"),
                "unit": entry.get("unit"),
                "raw": entry.get("raw"),
                "chunkIndex": entry.get("chunkIndex"),
            }
            for entry in entries
        ]
        candidate[key] = int(chosen["value_krw"])
    return fields, candidate, conflicts


def _stable_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


FEASIBILITY_INPUT_KEYS = {
    "total_revenue",
    "total_cost",
    "land_cost",
    "construction_cost",
    "finance_cost",
    "other_cost",
}


class FeasibilityReviewConflict(ValueError):
    """The browser's financial inputs are older than the stored review."""


def feasibility_review_state(project: Dict[str, Any]) -> Dict[str, Any]:
    from use_cases.cash_flow_timing import timing_source
    inputs = project.get("feasibility_input") or {}
    expected = project.get("expected_results") or {}
    values = {key: inputs.get(key) for key in FEASIBILITY_INPUT_KEYS}
    values.update(pf_amount=expected.get("total_pf_amount"), development_profit=expected.get("development_profit"))
    stored_reviews = (project.get("_meta") or {}).get("feasibilityFieldReviews") or {}
    reviews = {key: review for key, review in stored_reviews.items()
               if isinstance(review, dict) and review.get("value") == values.get(key)}
    terms = reviewed_terms(project)
    stored_term_reviews = (project.get("_meta") or {}).get("feasibilityTermReviews") or {}
    term_reviews = {key: review for key, review in stored_term_reviews.items()
                    if key in TERM_GROUPS and isinstance(review, dict) and review.get("value") == terms[key]}
    state = {"values": values, "fieldReviews": reviews, "terms": terms, "termReviews": term_reviews}
    # Include the complete working input, including product/collection and cost
    # sources used by reviewed scenarios. Archival document metadata and unrelated
    # audit/selection history do not change financial input freshness.
    calculation_inputs = {
        key: value for key, value in project.items()
        if key not in {"_meta", "source_documents", "sharedDealStateGraph"}
    }
    chief_state = calculation_inputs.get("chief_orchestration_state")
    if isinstance(chief_state, dict):
        # The workspace view can refresh both copies of this derived graph
        # without touching any reviewed input. Preserve other Chief state.
        working_state = {key: value for key, value in chief_state.items() if key != "sharedDealStateGraph"}
        if working_state:
            calculation_inputs["chief_orchestration_state"] = working_state
        else:
            calculation_inputs.pop("chief_orchestration_state", None)
    review_hash = _stable_json({**state,
        "calculationInputs": calculation_inputs,
        "parsingSource": (project.get("_meta") or {}).get("parsing_source")})
    source_study = next((project[key] for key in ("feasibilityStudyV2Draft", "canonicalFeasibilityStudyV2", "feasibilityStudyV2")
                         if isinstance(project.get(key), dict)), {})
    raw_import = source_study.get("raw_import")
    raw_import = raw_import if isinstance(raw_import, dict) else {}
    workbook_review = raw_import.get("financingTermReview")
    workbook_review = workbook_review if isinstance(workbook_review, dict) else {}
    workbook_review = ({**deepcopy(workbook_review), "sourceDocument": deepcopy(raw_import.get("sourceDocument") or {})}
                       if workbook_review.get("schemaVersion") in {"bonri-workbook-financing-review-v1", "sangbong-workbook-financing-review-v1"} else None)
    mapping = raw_import.get("financialMapping")
    financial_review = None
    if (raw_import.get("profile") in {"sangbong-sale-feasibility", "sangbong-private-rental-feasibility"}
            and isinstance(mapping, dict) and mapping.get("schemaVersion") == "sangbong-financial-mapping-v1"):
        fields = mapping.get("sourceFields")
        fields = fields if isinstance(fields, dict) else {}
        source = raw_import.get("sourceDocument")
        source = source if isinstance(source, dict) else {}
        financial_review = {
            **deepcopy(mapping),
            "profile": raw_import["profile"],
            "sourceWorkbook": raw_import.get("sourceWorkbook") or source.get("originalFileName"),
            "sourceDocument": {key: deepcopy(source[key]) for key in
                               ("archiveId", "originalFileName", "originalFileHash", "objectVersion") if key in source},
            "sourceFields": {key: deepcopy(value) for key, value in fields.items()
                             if key in EXTRACT_FIELDS and isinstance(value, dict)},
            "analysisOnly": True, "reportEligible": False,
        }
    return {**state, "reviewHash": review_hash, "analysisOnly": True, "reportEligible": False,
            "cashFlowTimingSource": timing_source(project),
            "workbookFinancingReview": workbook_review,
            "workbookFinancialReview": financial_review,
            "canonicalStudyPresent": bool(project.get("canonicalFeasibilityStudyV2") or project.get("feasibilityStudyV2"))}


def apply_feasibility_input_use_case(
    project_repository,
    project_id: str,
    feasibility_input: Dict[str, Any],
    *,
    applied_by: str,
    source_document: Dict[str, Any] | None = None,
    field_reviews: Dict[str, Any] | None = None,
    field_source_documents: Dict[str, Any] | None = None,
    expected_review_hash: str | None = None,
    terms: Dict[str, Any] | None = None,
    term_reviews: Dict[str, Any] | None = None,
    term_source_documents: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Persist a reviewer-confirmed feasibility extraction onto the project (human-gated).

    Revenue/cost fields go to ``feasibility_input`` (consumed by the cash-flow engine
    and the feasibility page); ``pf_amount``/``development_profit`` are routed to
    ``expected_results`` so the finance/report pages show real PF/profit too.
    """
    if not isinstance(feasibility_input, dict):
        raise ValueError("사업수지 입력은 항목별 숫자여야 합니다.")
    numeric = {}
    for key in EXTRACT_FIELDS:
        if key not in feasibility_input:
            continue
        value = feasibility_input[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or abs(value) > 9_007_199_254_740_991 or not math.isfinite(value)
                or value != int(value) or (key != "development_profit" and value < 0)):
            raise ValueError(f"{key}: 원 단위의 유효한 정수를 입력하세요.")
        numeric[key] = int(value)
    clean_terms = validate_terms(terms) if terms is not None else {}
    if not numeric and not clean_terms:
        raise ValueError("적용할 수치가 없습니다.")
    existing = project_repository.get(project_id)
    if not existing:
        raise LookupError("Project not found")

    prior = feasibility_review_state(existing)
    if clean_terms and (not isinstance(expected_review_hash, str) or not expected_review_hash):
        raise ValueError("최신 검토 상태를 불러온 뒤 저장하세요.")
    if expected_review_hash is not None and expected_review_hash != prior["reviewHash"]:
        raise FeasibilityReviewConflict("저장된 입력이 변경되었습니다. 최신 값을 다시 불러온 뒤 수정하세요.")
    if field_reviews is not None and not isinstance(field_reviews, dict):
        raise ValueError("항목별 검토 정보를 확인하세요.")
    reviews = dict(prior["fieldReviews"])
    applied_at = datetime.now(timezone.utc).isoformat()
    if term_reviews is not None and (not isinstance(term_reviews, dict) or term_reviews.keys() - TERM_GROUPS):
        raise ValueError("사업기간·대출 조건 검토 정보를 확인하세요.")
    if term_source_documents is not None and (not isinstance(term_source_documents, dict) or term_source_documents.keys() - TERM_GROUPS):
        raise ValueError("사업기간·대출 조건 원본 문서를 확인하세요.")
    terms_patch = patch_terms(existing, clean_terms)
    next_terms = reviewed_terms({**existing, **terms_patch})
    saved_term_reviews = dict(prior["termReviews"])
    changed_term_reviews = {}
    for group in clean_terms:
        review = (term_reviews or {}).get(group)
        if not isinstance(review, dict):
            raise ValueError(f"{group}: 수정 또는 가정의 이유를 입력하세요.")
        origin = review.get("origin")
        reason = review.get("reason")
        source_cell = review.get("sourceCell")
        document = (term_source_documents or {}).get(group) or source_document
        if not isinstance(origin, str) or origin not in {"user_assumption", "document_correction"}:
            raise ValueError(f"{group}: 입력 구분을 확인하세요.")
        if group == "cashFlowTiming" and origin != "user_assumption":
            raise ValueError("시간·수금 연결은 분석용 사용자 가정으로만 저장할 수 있습니다.")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            raise ValueError(f"{group}: 수정 또는 가정의 이유를 입력하세요(4000자 이하).")
        if source_cell is not None and (not isinstance(source_cell, str) or len(source_cell) > 1000):
            raise ValueError(f"{group}: 출처 셀을 확인하세요.")
        if origin == "document_correction" and (not isinstance(document, dict) or not document.get("archiveId") or not (source_cell or "").strip()):
            raise ValueError(f"{group}: 보관된 원본 문서와 출처 셀이 필요합니다.")
        changed_term_reviews[group] = {
            "value": next_terms[group], "previousValue": prior["terms"][group],
            "origin": origin, "reason": reason.strip(),
            "sourceCell": source_cell.strip() if origin == "document_correction" else None,
            "sourceDocument": document if origin == "document_correction" else None,
            "reviewedBy": applied_by, "reviewedAt": applied_at,
            "needsReview": True, "approvalStatus": "not_requested",
        }
        saved_term_reviews[group] = changed_term_reviews[group]
    changed_reviews = {}
    for key, value in numeric.items():
        field_document = (field_source_documents or {}).get(key) or source_document
        review = (field_reviews or {}).get(key) or {}
        if not isinstance(review, dict):
            raise ValueError(f"{key}: 검토 정보가 올바르지 않습니다.")
        origin = review.get("origin", "user_assumption")
        reason = str(review.get("reason") or "").strip()
        source_cell = str(review.get("sourceCell") or "").strip()
        if origin not in {"user_assumption", "document_correction"}:
            raise ValueError(f"{key}: 입력 구분을 확인하세요.")
        if field_reviews is not None and not reason:
            raise ValueError(f"{key}: 수정 또는 가정의 이유를 입력하세요.")
        if origin == "document_correction" and (not (field_document or {}).get("archiveId") or not source_cell):
            raise ValueError(f"{key}: 보관된 원본 문서와 출처 셀이 필요합니다.")
        changed_reviews[key] = {
            "value": value, "previousValue": prior["values"].get(key),
            "origin": origin, "reason": reason or None,
            "sourceCell": source_cell if origin == "document_correction" else None,
            "sourceDocument": field_document if origin == "document_correction" else None,
            "reviewedBy": applied_by, "reviewedAt": applied_at,
            "needsReview": True, "approvalStatus": "not_requested",
        }
        reviews[key] = changed_reviews[key]

    fi_update = {k: v for k, v in numeric.items() if k in FEASIBILITY_INPUT_KEYS}

    expected = dict(existing.get("expected_results") or {})
    if "pf_amount" in numeric:
        expected["total_pf_amount"] = numeric["pf_amount"]
    if "development_profit" in numeric:
        expected["development_profit"] = numeric["development_profit"]
    if {"development_profit", "total_revenue"} & numeric.keys() and expected.get("development_profit") is not None:
        revenue = fi_update.get("total_revenue", (existing.get("feasibility_input") or {}).get("total_revenue"))
        expected["profit_margin"] = round(expected["development_profit"] / float(revenue), 4) if revenue else None

    history = list(((existing.get("_meta") or {}).get("appliedFeasibilityInputs") or []))
    history.append({"appliedBy": applied_by, "appliedAt": applied_at, "fields": list(numeric.keys()),
                    "sourceDocument": source_document or {}, "fieldReviews": changed_reviews,
                    "termReviews": changed_term_reviews})

    update_payload: Dict[str, Any] = {
        "feasibility_input": {**(existing.get("feasibility_input") or {}), **fi_update},
        "_meta": {**(existing.get("_meta") or {}), "appliedFeasibilityInputs": history,
                  "feasibilityInputSource": "human_workbook_review", "feasibilityFieldReviews": reviews,
                  "feasibilityTermReviews": saved_term_reviews},
        "feasibility_input_sources": {
            **(existing.get("feasibility_input_sources") or {}),
            **{key: {"value": value, "source": reviews[key]["origin"], "needsReview": True,
                      "confidence": 0.6, "review": reviews[key]} for key, value in fi_update.items()},
        },
    }
    update_payload.update(terms_patch)
    if expected != (existing.get("expected_results") or {}):
        update_payload["expected_results"] = expected

    updated = project_repository.update(project_id, update_payload)
    if updated is None:
        raise LookupError("Project not found")
    return {
        "schemaVersion": "realytics-feasibility-input-apply-v1",
        "projectId": project_id,
        "appliedFields": numeric,
        "appliedTerms": list(clean_terms),
        "feasibility_input": (updated or {}).get("feasibility_input"),
        "expected_results": (updated or {}).get("expected_results"),
        "project": updated,
        "reviewState": feasibility_review_state(updated),
        "requiresRecalculation": True,
        "analysisOnly": True,
        "reportEligible": False,
    }
