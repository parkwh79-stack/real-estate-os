from __future__ import annotations

"""Smart-intake finalize pipeline.

After SmartIntake classifies + parses the uploaded documents, this chains the
remaining steps so the workspace is populated end-to-end (the user's "분류대로
처리 → 즉시 작업" expectation):

  1. Identity: keep the parsed real project name + land.address.
  2. Address -> PNU: geocode the project address (VWorld) and resolve the
     cadastral parcel, saving PNU / 공시지가(jiga) / center into the project.
  3. (caller) optionally run the track's analysis (PF review orchestration).

Non-fabricating: PNU/공시지가 come only from VWorld; if the address does not
resolve, nothing is invented — the project keeps whatever was parsed.
"""

from typing import Any, Dict, List, Optional

from integrations.vworld import fetch_parcel_by_point, geocode_address, vworld_configured


def _address_candidates(address: str) -> List[str]:
    raw = address.strip()
    candidates = [raw]
    cleaned = raw.replace("일원", "").strip()
    if cleaned and cleaned != raw:
        candidates.append(cleaned)
    tokens = cleaned.split()
    dong_idx = next((i for i, t in enumerate(tokens) if t.endswith(("동", "리", "가"))), -1)
    if dong_idx >= 0:
        candidates.append(" ".join(tokens[: dong_idx + 1]))
    return list(dict.fromkeys([c for c in candidates if c]))


def resolve_address_to_parcel(address: str, *, environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Geocode an address and resolve its cadastral parcel (PNU + 공시지가 + center).

    Returns {status, pnu, center, officialLandPricePerSqm, officialLandPriceBaseYear,
    lotNumber, landCategory, addressFragment}. status is 'resolved' only when a PNU
    is found; otherwise a diagnostic status (skipped/geocode_failed/parcel_failed).
    """
    if not address or not address.strip():
        return {"status": "no_address"}
    if not vworld_configured(environ):
        return {"status": "vworld_not_configured"}

    point = None
    geo_status = "geocode_failed"
    for cand in _address_candidates(address):
        point, gdiag = geocode_address(cand, environ=environ)
        geo_status = gdiag.get("status", "error")
        if point:
            break
    if not point:
        return {"status": geo_status or "geocode_failed"}

    geometry, pdiag = fetch_parcel_by_point(point["lng"], point["lat"], environ=environ)
    props = pdiag.get("properties") or {} if pdiag.get("status") == "resolved" else {}
    pnu = (props.get("pnu") or "").strip() or None

    jiga = props.get("jiga")
    try:
        jiga_val = float(str(jiga).replace(",", "")) if jiga not in (None, "") else None
    except (TypeError, ValueError):
        jiga_val = None

    return {
        "status": "resolved" if pnu else "parcel_unresolved",
        "pnu": pnu,
        "center": {"lng": point["lng"], "lat": point["lat"]},
        "officialLandPricePerSqm": jiga_val,
        "officialLandPriceBaseYear": props.get("gosi_year"),
        "lotNumber": props.get("jibun"),
        "landCategory": props.get("jimok") or props.get("dgm_nm"),
        "addressFragment": props.get("addr") or props.get("jibun") or address,
        "geometry": geometry,
    }


def finalize_project_intake_use_case(
    project_repository,
    project_id: str,
    *,
    environ: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Promote parsed identity and resolve+save the project's PNU from its address.

    Returns a summary {projectId, address, parcel, persisted}. Does NOT run the
    orchestration — the caller decides whether to trigger the track analysis.
    """
    project = project_repository.get(project_id)
    if not project:
        raise LookupError("Project not found")

    land = dict(project.get("land") or {})
    address = (land.get("address") or "").strip()

    # Fall back to a parcel addressFragment if no top-level address was parsed.
    if not address:
        for parcel in project.get("land_acquisition") or []:
            frag = (parcel.get("addressFragment") or parcel.get("address") or "").strip()
            if frag:
                address = frag
                break

    parcel = resolve_address_to_parcel(address, environ=environ)
    persisted = False
    if parcel.get("status") == "resolved":
        land["address"] = land.get("address") or address
        land["pnu"] = parcel["pnu"]
        if parcel.get("center"):
            land["center"] = parcel["center"]
        if parcel.get("officialLandPricePerSqm"):
            land["officialLandPricePerSqm"] = parcel["officialLandPricePerSqm"]
            land["officialLandPriceBaseYear"] = parcel.get("officialLandPriceBaseYear")
        project_repository.update(project_id, {"land": land})
        persisted = True

    return {
        "projectId": project_id,
        "address": address or None,
        "parcel": {k: v for k, v in parcel.items() if k != "geometry"},
        "persisted": persisted,
    }
