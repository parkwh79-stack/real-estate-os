from __future__ import annotations

"""Chief Deal Room: pod-input auto-supplementation.

When a pod is blocked on missing inputs, the orchestrator should first try to
self-serve those inputs from legitimate sources — the already-parsed project
data and public APIs (VWorld cadastral, MOLIT 실거래가) — before asking the
user for more uploads. This module implements that attempt.

Non-fabrication contract (AGENTS.md): every supplemented value comes from a
named external source with provenance recorded in the returned log and in
``project_data["autoSupplementLog"]``. Nothing is invented; anything that
cannot be fetched stays missing and is reported honestly.
"""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from integrations.market_data import MolitRtmsConnector, lawd_cd_from_pnu
from use_cases.intake_finalize import resolve_address_to_parcel
from use_cases.market_comparables import (
    fetch_project_market_comparables,
    inject_market_comparables,
    recent_deal_yms,
)

# item_ids from chief_deal_orchestration._missing_inputs that we can try to self-serve.
AUTO_SERVABLE_ITEMS = {
    "pnu_or_land_ledger_missing",
    "land_evidence_missing",
    "market_evidence_missing",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_address(project_data: Dict[str, Any]) -> str:
    land = project_data.get("land") or {}
    address = str(land.get("address") or "").strip()
    if address:
        return address
    for parcel in project_data.get("land_acquisition") or []:
        if isinstance(parcel, dict):
            frag = str(parcel.get("addressFragment") or parcel.get("address") or "").strip()
            if frag:
                return frag
    return ""


def _supplement_land(
    project_id: str,
    project_data: Dict[str, Any],
    *,
    resolver: Callable[[str], Dict[str, Any]],
    address_miner: Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Address (parsed from the business plan) -> PNU + 공시지가 via VWorld."""
    land = dict(project_data.get("land") or {})
    if land.get("pnu") and land.get("officialLandPricePerSqm"):
        return {"itemId": "pnu_or_land_ledger_missing", "status": "already_present"}

    address = _project_address(project_data)
    address_provenance: Optional[Dict[str, Any]] = None
    if not address:
        # LLM parse missed it — mine the archived document text deterministically
        # (handles no-space cover addresses like "경기도용인시기흥구상갈동13번지일대").
        mined = address_miner(project_id, project_data)
        if mined:
            address = mined["address"]
            address_provenance = mined.get("provenance")
    if not address:
        return {
            "itemId": "pnu_or_land_ledger_missing",
            "status": "needs_user_material",
            "reason": "사업계획서 파싱과 본문 주소 추출 모두에서 주소를 찾지 못해 PNU를 자동 해석할 수 없습니다.",
        }

    try:
        parcel = resolver(address)
    except Exception:
        return {"itemId": "pnu_or_land_ledger_missing", "status": "fetch_failed",
                "source": "VWorld 연속지적도", "reason": "공개 토지자료를 조회하지 못했습니다."}
    if parcel.get("status") != "resolved":
        return {
            "itemId": "pnu_or_land_ledger_missing",
            "status": "fetch_failed",
            "source": "VWorld 연속지적도",
            "reason": f"주소 '{address}' 해석 실패 ({parcel.get('status')})",
        }

    if land.get("pnu") and str(land["pnu"]) != str(parcel.get("pnu")):
        return {"itemId": "pnu_or_land_ledger_missing", "status": "needs_user_material",
                "reason": "주소 조회 PNU와 기존 필지가 다릅니다. 기존 입력을 보존하고 대상 필지 확인을 요청합니다."}
    land["address"] = land.get("address") or address
    land["pnu"] = parcel["pnu"]
    if parcel.get("center") and not land.get("center"):
        land["center"] = parcel["center"]
    if parcel.get("officialLandPricePerSqm") and land.get("officialLandPricePerSqm") is None:
        land["officialLandPricePerSqm"] = parcel["officialLandPricePerSqm"]
        land["officialLandPriceBaseYear"] = parcel.get("officialLandPriceBaseYear")
    project_data["land"] = land
    result: Dict[str, Any] = {
        "itemId": "pnu_or_land_ledger_missing",
        "status": "supplemented",
        "source": "VWorld 연속지적도 (공공 API)",
        "fetchedAt": _now(),
        "values": {
            "pnu": parcel.get("pnu"),
            "officialLandPricePerSqm": parcel.get("officialLandPricePerSqm"),
            "officialLandPriceBaseYear": parcel.get("officialLandPriceBaseYear"),
            "address": address,
        },
    }
    if address_provenance:
        result["addressProvenance"] = address_provenance
    return result


def _supplement_market(
    project_data: Dict[str, Any],
    *,
    connector_factory: Callable[[], Any],
) -> Dict[str, Any]:
    """PNU -> 시군구 실거래가 comparables via MOLIT open API."""
    market = project_data.get("market") or {}
    if isinstance(market, dict) and market.get("transactionComparables"):
        return {"itemId": "market_evidence_missing", "status": "already_present"}

    land = project_data.get("land") or {}
    lawd = lawd_cd_from_pnu(land.get("pnu"))
    if not lawd:
        return {
            "itemId": "market_evidence_missing",
            "status": "blocked_by_dependency",
            "reason": "PNU가 없어 시군구 코드를 도출할 수 없습니다 (토지 보강 선행 필요).",
        }

    try:
        records, diag = fetch_project_market_comparables(
            project_data,
            connector=connector_factory(),
            deal_yms=recent_deal_yms(3),
            lawd_cd=lawd,
        )
    except Exception as exc:  # network/HTTP errors must never break the pod run
        return {
            "itemId": "market_evidence_missing",
            "status": "fetch_failed",
            "source": "국토부 실거래가 공개 API",
            "reason": str(exc)[:160],
        }
    if not records:
        return {
            "itemId": "market_evidence_missing",
            "status": "fetch_failed",
            "source": "국토부 실거래가 공개 API",
            "reason": diag.get("reason") or "해당 시군구·기간의 실거래 데이터가 없습니다.",
        }
    injected = inject_market_comparables(project_data, records)
    return {
        "itemId": "market_evidence_missing",
        "status": "supplemented",
        "source": "국토부 실거래가 공개 API",
        "fetchedAt": _now(),
        "values": {"transactionComparableCount": injected, "lawdCd": lawd},
    }


def auto_supplement_pod_inputs(
    project_id: str,
    project_data: Dict[str, Any],
    *,
    resolver: Optional[Callable[[str], Dict[str, Any]]] = None,
    connector_factory: Optional[Callable[[], Any]] = None,
    address_miner: Optional[Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Try to self-serve missing pod inputs from public sources, mutating
    ``project_data`` in place. Returns the attempt log (also appended to
    ``project_data['autoSupplementLog']`` for the audit trail)."""
    from use_cases.address_mining import mine_project_address

    resolver = resolver or (lambda address: resolve_address_to_parcel(address))
    connector_factory = connector_factory or MolitRtmsConnector
    address_miner = address_miner or mine_project_address

    attempts = [
        _supplement_land(project_id, project_data, resolver=resolver, address_miner=address_miner),
        _supplement_market(project_data, connector_factory=connector_factory),
    ]
    log = [a for a in attempts if a.get("status") != "already_present"]
    if log:
        existing = project_data.get("autoSupplementLog")
        project_data["autoSupplementLog"] = ([*existing] if isinstance(existing, list) else []) + [
            {"projectId": project_id, "attemptedAt": _now(), "attempts": log}
        ]
    return log
