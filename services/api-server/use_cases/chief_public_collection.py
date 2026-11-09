"""Bounded public land acquisition for the transient Chief review projection."""
from copy import deepcopy
from time import monotonic
from typing import Any

from integrations.land_data_connectors import LandDataConnectorRouter
from use_cases.parcel_pnu_resolution import canonical_project_parcel_rows, parcel_pnu


LAND_RESULTS = {
    "land_use_planning": ("landUsePlanningRecords", "용도지역·토지이용계획"),
    "official_land_price": ("officialLandPrices", "공시지가"),
    "land_characteristics": ("landCharacteristicsRecords", "토지 특성"),
}


def collect_land_evidence(
    source: dict[str, Any], confirmed: set[str], previous: dict[str, str], *,
    deadline: float, retry_failed: bool,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Enrich a caller-owned copy; no repository, approval, or raw result writes."""
    tasks = deepcopy(previous)
    evidence = deepcopy(source.get("landEvidence") or source.get("land_evidence") or {})
    records = list(evidence.get("records") or [])
    parcels = canonical_project_parcel_rows(source) or [source.get("land") or {}]
    by_pnu = {parcel_pnu(row): row for row in parcels if parcel_pnu(row) in confirmed}
    router = LandDataConnectorRouter()
    fetched_parcels = 0
    for pnu, parcel in by_pnu.items():
        needed = set()
        for kind in LAND_RESULTS:
            key = f"{pnu}:{kind}"
            if any(row.get("pnu") == pnu and row.get("evidenceType") == kind
                   and row.get("sourcePnuVerified") is True for row in records):
                tasks[key] = "available"
            elif key not in tasks or tasks[key] == "deferred" or (tasks[key] == "failed" and retry_failed):
                needed.add(kind)
        if not needed:
            continue
        if fetched_parcels >= 2 or monotonic() >= deadline:
            tasks.update({f"{pnu}:{kind}": "deferred" for kind in needed})
            continue
        fetched_parcels += 1
        try:
            response = router.fetch_records_for_parcels(
                [{**parcel, "pnu": pnu}], evidence_types=needed, deadline=deadline,
                include_region_trends=False, timeout_seconds=2.0,
            )
        except Exception:
            tasks.update({f"{pnu}:{kind}": "failed" for kind in needed})
            continue
        for kind in needed:
            output_key = LAND_RESULTS[kind][0]
            returned = response.get(output_key) or []
            matches = [row for row in returned if row.get("pnu") == pnu and row.get("sourcePnuVerified") is True]
            diagnostics = {str(row.get("status")) for row in response.get("diagnostics") or []
                           if row.get("connector") == kind}
            if matches:
                for row in matches:
                    records.append({**deepcopy(row), "evidenceType": kind, "sourceLocator": row.get("sourceLocator") or {
                        "sourceType": "api_row", "apiRowId": pnu,
                        "reference": row.get("sourceUrlOrEvidenceId"), "sourceName": row.get("sourceName"),
                    }})
                status = "checked"
            elif "deferred" in diagnostics:
                status = "deferred"
            elif diagnostics and all(value.startswith("skipped_missing_") for value in diagnostics):
                status = "unavailable"
            elif returned or any("fail" in value or "mismatch" in value or "unverified" in value for value in diagnostics):
                status = "failed"
            else:
                status = "empty"
            tasks[f"{pnu}:{kind}"] = status
    if records:
        evidence["records"] = records
        source["landEvidence"] = evidence
    lookups = []
    messages = {
        "checked": "요청 PNU와 일치하는 공공 근거를 이번 검토에 연결했습니다.",
        "available": "확인된 기존 공공 근거를 재사용했습니다.",
        "deferred": "남은 필지의 공개 조회를 이어서 확인합니다.",
        "failed": "일부 필지 조회에 실패했습니다. 연결·응답을 확인한 뒤 재시도해 주세요.",
        "unavailable": "공공 연결 설정이 없어 자동 확인할 수 없습니다.",
        "empty": "조회했지만 사용할 근거가 없습니다. 해당 원본 자료를 확인해 주세요.",
        "needs_input": "사업 대상 PNU 확인 후 자동 조회합니다.",
    }
    for kind, (_, label) in LAND_RESULTS.items():
        statuses = [tasks.get(f"{pnu}:{kind}", "deferred") for pnu in by_pnu]
        status = next((candidate for candidate in ("failed", "deferred", "unavailable", "empty", "checked", "available")
                       if candidate in statuses), "needs_input")
        lookups.append({"key": kind, "podKey": "land_legal", "label": label, "status": status,
                        "message": messages[status], "checkedCount": sum(value in {"checked", "available"} for value in statuses),
                        "requestedCount": len(by_pnu)})
    return lookups, tasks
