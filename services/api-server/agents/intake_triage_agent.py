from __future__ import annotations

"""AI-assisted track triage for the OS launcher.

The agent receives only classified document metadata from SmartIntake. It does
not parse document contents or invent project facts. Deterministic scoring is
always available; an LLM may coordinate the recommendation when configured, but
only from the same document signals.
"""

import json
import os
from typing import Any, Dict, Iterable, List

from agents.agent_policy import system_prompt_for
from agents.ai_connection import AIConnection, AIExtractionResult, safe_ai_context
from agents.ai_router import describe_selection, select_ai_context

TRACKS: Dict[str, Dict[str, Any]] = {
    "development": {
        "label": "부동산개발",
        "title": "부동산 개발",
        "description": "토지, 인허가, 사업수지, 현금흐름 중심으로 개발 가능성을 검토합니다.",
        "entryHref": "input-package",
        "menuKeys": ["dashboard", "templates", "analysis", "feasibility", "cashflow", "report", "glossary"],
    },
    "pf": {
        "label": "PF",
        "title": "부동산 PF",
        "description": "사업수지, 트랜치, 상환재원, 리스크, 심사보고서 중심으로 검토합니다.",
        "entryHref": "orchestration",
        "menuKeys": ["dashboard", "finance", "cashflow", "report", "monitoring", "glossary"],
    },
    "finance": {
        "label": "금융",
        "title": "부동산 금융",
        "description": "담보, 리파이낸싱, 대출구조, 회수 가능성 중심으로 검토합니다.",
        "entryHref": "finance",
        "menuKeys": ["dashboard", "finance", "cashflow", "report", "monitoring", "glossary"],
    },
    "real_asset_finance": {
        "label": "부동산 실물금융",
        "title": "부동산 실물금융",
        "description": "자산 실사, 임대/시장 증빙, 등기·권리관계, 인수 리스크를 검토합니다.",
        "entryHref": "input-package",
        "menuKeys": ["dashboard", "analysis", "finance", "report", "monitoring", "glossary"],
    },
    "reit": {
        "label": "리츠",
        "title": "리츠",
        "description": "편입자산, 배당재원, 임대수익, 감정가, 운용구조와 공시 리스크를 검토합니다.",
        "entryHref": "finance",
        "menuKeys": ["dashboard", "analysis", "finance", "report", "monitoring", "glossary"],
    },
    "fund": {
        "label": "펀드",
        "title": "부동산 펀드",
        "description": "펀드 설정, 투자대상, 대출/에쿼티 구조, 운용·회수 리스크를 검토합니다.",
        "entryHref": "finance",
        "menuKeys": ["dashboard", "finance", "cashflow", "report", "monitoring", "glossary"],
    },
}

DOC_TYPE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "business_plan": {"development": 3.0, "pf": 2.0},
    "basic_architectural_plan": {"development": 3.0, "pf": 1.0},
    "land_ledger": {"development": 2.5, "pf": 1.5, "real_asset_finance": 1.0},
    "feasibility_workbook": {"development": 2.5, "pf": 3.0, "finance": 1.0},
    "risk_report_workbook": {"pf": 3.0, "finance": 2.0, "fund": 1.0},
    "land_evidence": {"real_asset_finance": 2.5, "development": 1.0, "pf": 1.0, "finance": 0.5},
    "market_evidence": {"real_asset_finance": 2.0, "development": 1.0, "pf": 1.0, "reit": 1.0},
    "unknown": {},
}

FILENAME_HINTS: Dict[str, Dict[str, float]] = {
    "pf": {"pf": 2.0, "finance": 0.5},
    "브릿지": {"finance": 2.0, "pf": 1.0},
    "리파이": {"finance": 2.5},
    "refinance": {"finance": 2.5},
    "담보": {"finance": 2.0, "real_asset_finance": 0.5},
    "대출": {"finance": 2.0, "pf": 1.0},
    "rentroll": {"real_asset_finance": 3.0, "reit": 1.5},
    "rent_roll": {"real_asset_finance": 3.0, "reit": 1.5},
    "임대": {"real_asset_finance": 2.5, "reit": 1.0},
    "감정": {"real_asset_finance": 2.0, "finance": 1.0, "reit": 1.0},
    "인수": {"real_asset_finance": 2.5},
    "매입": {"real_asset_finance": 2.0},
    "리츠": {"reit": 3.0, "real_asset_finance": 1.0},
    "reit": {"reit": 3.0, "real_asset_finance": 1.0},
    "배당": {"reit": 2.5},
    "펀드": {"fund": 3.0, "finance": 1.0},
    "fund": {"fund": 3.0, "finance": 1.0},
    "집합투자": {"fund": 2.5},
    "분양": {"development": 1.0, "pf": 1.0},
}


def triage_intake_documents(
    files: Iterable[Dict[str, Any]],
    *,
    ai_context: Dict[str, Any] | None = None,
    ai: AIConnection | None = None,
) -> Dict[str, Any]:
    normalized = [_normalize_file(item) for item in files]
    deterministic = _deterministic_triage(normalized)
    ai_result = _try_ai_triage(normalized, ai_context=ai_context, ai=ai)

    recommended = deterministic["recommendedTrack"]
    recommendation_source = "deterministic"
    if ai_result.get("recommended") in TRACKS:
        recommended = ai_result["recommended"]
        recommendation_source = "llm"

    tracks = []
    for track_key, base in deterministic["tracksByKey"].items():
        ai_rationale = (ai_result.get("rationale") or {}).get(track_key)
        tracks.append(
            {
                **TRACKS[track_key],
                "track": track_key,
                "score": base["score"],
                "confidence": base["confidence"],
                "recommended": track_key == recommended,
                "rationale": ai_rationale or base["rationale"],
                "reasons": base["reasons"],
            }
        )

    tracks.sort(key=lambda item: (not item["recommended"], -item["score"], item["track"]))
    return {
        "schemaVersion": "realytics-intake-triage-v1",
        "recommendedTrack": recommended,
        "recommendationSource": recommendation_source,
        "aiAssisted": ai_result.get("aiAssisted", False),
        "aiSelection": ai_result.get("aiSelection"),
        "aiError": ai_result.get("aiError"),
        "inputSignals": normalized,
        "tracks": tracks,
        "limitations": [
            "트랙 추천은 업로드 파일명, 문서유형, 분류 신뢰도만 기준으로 합니다.",
            "자료 내용의 수치·권리관계·금융조건 판단은 각 전담 에이전트 검토 후 확정해야 합니다.",
        ],
    }


def _normalize_file(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "filename": item.get("filename") or item.get("fileName") or item.get("name") or "upload",
        "documentType": item.get("documentType") or "unknown",
        "label": item.get("label") or item.get("documentLabel") or item.get("documentType") or "미분류",
        "confidence": _float(item.get("confidence"), default=0.0),
        "needsReview": bool(item.get("needsReview")),
        "reasons": item.get("reasons") if isinstance(item.get("reasons"), list) else [],
    }


def _deterministic_triage(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    scores = {track: 0.0 for track in TRACKS}
    reasons: Dict[str, List[str]] = {track: [] for track in TRACKS}

    for item in files:
        doc_type = item["documentType"]
        confidence = max(0.25, min(1.0, item["confidence"] or 0.0))
        for track, weight in DOC_TYPE_WEIGHTS.get(doc_type, {}).items():
            add = round(weight * confidence, 3)
            scores[track] += add
            reasons[track].append(f"{item['label']}({item['filename']}) 신호 +{add:g}")

        normalized_name = str(item["filename"]).lower().replace(" ", "")
        for cue, weights in FILENAME_HINTS.items():
            if cue in normalized_name:
                for track, weight in weights.items():
                    scores[track] += weight
                    reasons[track].append(f"파일명 '{cue}' 신호 +{weight:g}")

    if not files:
        reasons["development"].append("업로드 파일이 없어 기본 개발 검토 트랙을 제안")

    recommended = max(scores.items(), key=lambda pair: (pair[1], pair[0] == "development"))[0]
    tracks_by_key = {}
    max_score = max(scores.values()) or 1.0
    for track in TRACKS:
        score = round(scores[track], 3)
        confidence = round(min(0.98, score / max(max_score, 1.0)), 2)
        if score <= 0:
            rationale = "해당 트랙을 뒷받침하는 문서 신호가 약합니다."
        elif track == recommended:
            rationale = f"{', '.join(_doc_labels(files)) or '업로드 자료'} 기준으로 가장 관련성이 높습니다."
        else:
            rationale = "일부 관련 신호는 있으나 추천 트랙보다 근거가 약합니다."
        tracks_by_key[track] = {
            "score": score,
            "confidence": confidence,
            "rationale": rationale,
            "reasons": reasons[track][:5],
        }
    return {"recommendedTrack": recommended, "tracksByKey": tracks_by_key}


def _try_ai_triage(
    files: List[Dict[str, Any]],
    *,
    ai_context: Dict[str, Any] | None,
    ai: AIConnection | None,
) -> Dict[str, Any]:
    preferred = os.environ.get("REALYTICS_TRIAGE_AI_PROVIDER") or "gemini"
    selected = ai_context or select_ai_context(preferred=preferred, tier="triage")
    if not selected:
        return {
            "aiAssisted": False,
            "aiSelection": describe_selection(None),
            "aiError": "no_configured_ai_provider",
        }

    prompt = json.dumps(
        {
            "tracks": {key: {"label": value["label"], "description": value["description"]} for key, value in TRACKS.items()},
            "classifiedDocuments": files,
        },
        ensure_ascii=False,
    )
    client = ai or AIConnection()
    result: AIExtractionResult = client.extract_json(
        prompt,
        system=system_prompt_for(
            "triage",
            base="You are the intake triage coordinator for REALYTICS OS. Output only JSON.",
        ),
        ai_context=selected,
    )
    if result.data is None:
        return {
            "aiAssisted": False,
            "aiSelection": describe_selection(selected),
            "aiError": result.error,
        }
    recommended = result.data.get("recommended")
    rationale = result.data.get("rationale") if isinstance(result.data.get("rationale"), dict) else {}
    return {
        "aiAssisted": True,
        "aiSelection": describe_selection(selected),
        "recommended": recommended if recommended in TRACKS else None,
        "rationale": {key: str(value) for key, value in rationale.items() if key in TRACKS},
        "raw": safe_ai_context(result.data),
    }


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _doc_labels(files: List[Dict[str, Any]]) -> List[str]:
    labels = []
    for item in files:
        label = item.get("label")
        if label and label not in labels:
            labels.append(str(label))
    return labels[:3]
