from __future__ import annotations

"""Batch PNU resolution for every parcel in the project's land ledger.

The single-address finalize resolves ONE representative PNU. A 토지조서 has many
지번 — each parcel needs its own PNU/공시지가 so the land pod, map, and legal
review work per-필지. This walks parcels missing a PNU, builds a full address
from the project's address context + the parcel's lot fragment, and resolves
each through VWorld. Results carry per-parcel provenance; failures stay honest
("unresolved" with the attempted address) — nothing is invented.
"""

import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# Re-exported for callers that treat this module as the parcel-resolution API.
from use_cases.intake_finalize import resolve_address_to_parcel  # noqa: F401

# "경기도 용인시 기흥구 상갈동 13" -> prefix "경기도 용인시 기흥구" (up to the last 동/읍/면/리/가)
_PREFIX_RE = re.compile(r"^(?P<prefix>.*?[가-힣]{1,10}(?:동|읍|면|리|가))(?:\s|$)")


def _address_prefix(land_address: str) -> str:
    match = _PREFIX_RE.match((land_address or "").strip())
    if not match:
        return ""
    prefix = match.group("prefix").strip()
    # Drop a trailing jibun if the dong token itself captured none (prefix ends at 동).
    return prefix


def _dong_of(prefix: str) -> str:
    tokens = prefix.split()
    return tokens[-1] if tokens else ""


_LOT_RE = re.compile(r"(산)?\s*(\d+)(?:-(\d+))?")


def _lot_key(value: Any) -> tuple | None:
    """Normalize a 지번 ("13", "13-2", "산13", "13대") to (san, main, sub) for comparison."""
    match = _LOT_RE.search(str(value or ""))
    if not match:
        return None
    return (bool(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _resolved_lot_matches(requested: str, resolved_lot: Any) -> bool | None:
    """True/False when both lots are comparable; None when unverifiable."""
    want = _lot_key(requested)
    got = _lot_key(resolved_lot)
    if want is None or got is None:
        return None
    return want == got


def _parcel_lot(parcel: Dict[str, Any]) -> str:
    return str(
        parcel.get("normalizedLotNumber")
        or parcel.get("lotNumber")
        or parcel.get("rawLotNumber")
        or ""
    ).strip()


def _candidate_address(parcel: Dict[str, Any], prefix: str) -> str:
    """Full address for one parcel: project prefix + parcel dong/lot fragment."""
    fragment = str(parcel.get("addressFragment") or "").strip()
    lot = _parcel_lot(parcel)
    dong = str(parcel.get("dong") or "").strip()

    # If the fragment already carries a 시/구, it is a full-enough address.
    if fragment and any(t.endswith(("시", "구", "군")) for t in fragment.split()):
        return fragment
    if not prefix:
        return fragment or " ".join(p for p in [dong, lot] if p)

    prefix_dong = _dong_of(prefix)
    if dong and prefix_dong and dong != prefix_dong:
        # Parcel belongs to a different dong: swap the prefix's dong.
        base = prefix.rsplit(" ", 1)[0] if " " in prefix else ""
        return " ".join(p for p in [base, dong, lot] if p)
    if lot:
        return f"{prefix} {lot}"
    return fragment and f"{prefix.rsplit(' ', 1)[0]} {fragment}" or prefix


def _iter_parcel_lists(project: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    lists: List[List[Dict[str, Any]]] = []
    acquisition = project.get("land_acquisition")
    if isinstance(acquisition, list):
        lists.append(acquisition)
    ledger = project.get("landLedger") or project.get("land_ledger")
    if isinstance(ledger, dict) and isinstance(ledger.get("parcels"), list):
        lists.append(ledger["parcels"])
    return lists


def _parcel_identity(parcel: Dict[str, Any]) -> tuple:
    """Stable identity for a parcel so the same 지번 stored in both
    land_acquisition and landLedger.parcels is resolved once, not twice."""
    lot_key = _lot_key(_parcel_lot(parcel))
    dong = str(parcel.get("dong") or "").strip()
    if lot_key is not None:
        return ("lot", dong, lot_key)
    return ("frag", str(parcel.get("addressFragment") or "").strip())


def _grouped_parcels(project: Dict[str, Any]) -> "List[List[Dict[str, Any]]]":
    """Group every parcel dict (across both lists) by identity, preserving order.

    Returns a list of groups; each group is the set of dict objects that refer to
    the same physical parcel, so resolving once can be written back to all of them.
    """
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    order: List[tuple] = []
    for parcels in _iter_parcel_lists(project):
        for parcel in parcels:
            if not isinstance(parcel, dict):
                continue
            key = _parcel_identity(parcel)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(parcel)
    return [groups[key] for key in order]


def _pnu_lookup_to_result(props: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a VWorld cadastral property bag into the resolver result shape."""
    jiga = props.get("jiga")
    try:
        jiga_val = float(str(jiga).replace(",", "")) if jiga not in (None, "", "0") else None
    except (TypeError, ValueError):
        jiga_val = None
    return {
        "status": "resolved",
        "pnu": (props.get("pnu") or "").strip() or None,
        "officialLandPricePerSqm": jiga_val,
        "officialLandPriceBaseYear": props.get("gosi_year") or props.get("stdrYear"),
        "lotNumber": props.get("jibun"),
        "landCategory": props.get("jimok") or props.get("dgm_nm"),
        "addressFragment": props.get("addr") or props.get("jibun"),
    }


def resolve_project_parcels_use_case(
    project_repository,
    project_id: str,
    *,
    resolver: Optional[Callable[[str], Dict[str, Any]]] = None,
    dong_code_resolver: Optional[Callable[[str], Optional[str]]] = None,
    pnu_lookup: Optional[Callable[[str], Dict[str, Any]]] = None,
    limit: int = 30,
) -> Dict[str, Any]:
    """Resolve a PNU + 공시지가 for every parcel that lacks one (up to ``limit``).

    Primary (precise) strategy: geocode the 동 once to obtain its 10-digit
    법정동코드, then construct each parcel's PNU from its 지번 and verify it against
    VWorld's cadastre — no geocode→point→point-in-polygon ambiguity, so a wrong
    neighbor parcel is never attached. A 지번 that isn't in the current cadastre
    (e.g. merged/renumbered in redevelopment) stays honestly unresolved.

    Legacy fallback: when a caller injects only ``resolver`` (address→parcel), or
    when the 법정동코드 cannot be determined, the per-address resolver is used with
    the lot-match guard.

    Mutates the parcel dicts in place and persists once. Returns a summary.
    """
    # Legacy mode preserves the address-resolver path for callers/tests that
    # inject only `resolver` (no construction tools) — keeps them offline & stable.
    legacy_mode = resolver is not None and dong_code_resolver is None and pnu_lookup is None
    if resolver is None:
        from use_cases.intake_finalize import resolve_address_to_parcel

        resolver = resolve_address_to_parcel
    if not legacy_mode:
        from integrations.vworld import fetch_parcel_by_pnu, resolve_legal_dong_code, build_pnu

        dong_code_resolver = dong_code_resolver or (lambda addr: resolve_legal_dong_code(addr))
        pnu_lookup = pnu_lookup or (
            lambda pnu: (lambda props, diag: _pnu_lookup_to_result(props) if diag.get("status") == "resolved" else {"status": diag.get("status", "not_found")})(*fetch_parcel_by_pnu(pnu))
        )

    project = project_repository.get(project_id)
    if not project:
        raise LookupError("Project not found")

    prefix = _address_prefix(str((project.get("land") or {}).get("address") or ""))
    now = datetime.now(timezone.utc).isoformat()

    # Determine the 법정동코드 once (construction mode only).
    legal_dong_code: Optional[str] = None
    if not legacy_mode and prefix:
        # Geocode the dong prefix; append the first parcel lot to help VWorld parse.
        first_lot = ""
        for grp in _grouped_parcels(project):
            first_lot = _parcel_lot(grp[0])
            if first_lot:
                break
        probe = f"{prefix} {first_lot}".strip() if first_lot else prefix
        legal_dong_code = dong_code_resolver(probe) or dong_code_resolver(prefix)
    # The 법정동코드 above belongs to the project address's 동. A parcel in a
    # DIFFERENT 동 must NOT be constructed from it (that would stamp a wrong-동
    # parcel's PNU/공시지가 as "verified"). Such parcels fall through to the
    # address resolver, which swaps the 동 per parcel.
    prefix_dong = _dong_of(prefix)

    results: List[Dict[str, Any]] = []
    resolved_count = 0
    attempted = 0
    changed = False

    def _apply(group: List[Dict[str, Any]], patch: Dict[str, Any]) -> None:
        for member in group:
            member.update(patch)

    for group in _grouped_parcels(project):
        head = group[0]
        label = head.get("addressFragment") or _parcel_lot(head)
        if any(p.get("pnu") for p in group):
            existing = next((p.get("pnu") for p in group if p.get("pnu")), None)
            results.append({"parcel": label, "status": "already_resolved", "pnu": existing})
            continue
        if attempted >= limit:
            results.append({"parcel": label, "status": "skipped_limit"})
            continue

        # --- Primary: precise PNU construction from the 법정동코드 + 지번 ---------
        head_dong = str(head.get("dong") or "").strip()
        same_dong = not (head_dong and prefix_dong and head_dong != prefix_dong)
        if not legacy_mode and legal_dong_code and same_dong:
            from integrations.vworld import build_pnu

            constructed = build_pnu(legal_dong_code, _parcel_lot(head))
            if constructed:
                attempted += 1
                changed = True
                looked_up = pnu_lookup(constructed)
                if looked_up.get("status") == "resolved":
                    patch: Dict[str, Any] = {
                        "pnu": looked_up.get("pnu") or constructed,
                        "pnuResolution": {
                            "status": "resolved",
                            "constructedPnu": constructed,
                            "source": "VWorld 지적도 PNU 직접조회 (법정동코드+지번)",
                            "resolvedAt": now,
                            "lotVerification": "verified",
                        },
                    }
                    if looked_up.get("officialLandPricePerSqm"):
                        patch["officialLandPricePerSqm"] = looked_up["officialLandPricePerSqm"]
                        patch["officialLandPriceBaseYear"] = looked_up.get("officialLandPriceBaseYear")
                        patch["officialLandPriceSource"] = "VWorld 개별공시지가 (공공 API)"
                    _apply(group, patch)
                    resolved_count += 1
                    results.append({
                        "parcel": label, "status": "resolved", "pnu": patch["pnu"],
                        "officialLandPricePerSqm": looked_up.get("officialLandPricePerSqm"),
                        "method": "pnu_construction",
                    })
                else:
                    # The 지번 does not exist in the current cadastre (commonly
                    # merged/renumbered in redevelopment). Honest, not fabricated.
                    reason = "not_in_current_cadastre" if looked_up.get("status") == "not_found" else looked_up.get("status", "lookup_failed")
                    _apply(group, {"pnuResolution": {"status": "unresolved", "constructedPnu": constructed, "reason": reason, "attemptedAt": now}})
                    results.append({"parcel": label, "status": "unresolved", "constructedPnu": constructed, "reason": reason, "method": "pnu_construction"})
                continue
            # build_pnu failed (malformed 지번) → fall through to address resolver.

        # --- Fallback: address geocode → point → parcel (with lot-match guard) ----
        address = _candidate_address(head, prefix)
        if not address.strip():
            results.append({"parcel": label, "status": "no_address"})
            continue

        attempted += 1
        resolved = resolver(address)
        # Geocoders can silently fall back to dong-level coordinates and return a
        # NEIGHBORING parcel — attaching a wrong PNU to lender data is fabrication.
        # Reject when the resolved 지번 contradicts the requested one; flag
        # "unverified" when it cannot be compared.
        lot_check = (
            _resolved_lot_matches(_parcel_lot(head), resolved.get("lotNumber"))
            if resolved.get("status") == "resolved"
            else None
        )
        changed = True
        if resolved.get("status") == "resolved" and lot_check is False:
            reason = f"lot_mismatch(resolved 지번={resolved.get('lotNumber')})"
            _apply(group, {"pnuResolution": {"status": "unresolved", "attemptedAddress": address, "reason": reason, "attemptedAt": now}})
            results.append({"parcel": label, "status": "unresolved", "address": address, "reason": reason})
        elif resolved.get("status") == "resolved":
            patch: Dict[str, Any] = {
                "pnu": resolved["pnu"],
                "pnuResolution": {
                    "status": "resolved",
                    "resolvedAddress": address,
                    "source": "VWorld 연속지적도 (공공 API)",
                    "resolvedAt": now,
                    "lotVerification": "verified" if lot_check else "unverified",
                },
            }
            if resolved.get("officialLandPricePerSqm"):
                patch["officialLandPricePerSqm"] = resolved["officialLandPricePerSqm"]
                patch["officialLandPriceBaseYear"] = resolved.get("officialLandPriceBaseYear")
                patch["officialLandPriceSource"] = "VWorld 연속지적도 (공공 API)"
            _apply(group, patch)
            resolved_count += 1
            results.append({
                "parcel": label,
                "status": "resolved",
                "address": address,
                "pnu": resolved["pnu"],
                "officialLandPricePerSqm": resolved.get("officialLandPricePerSqm"),
            })
        else:
            _apply(group, {"pnuResolution": {"status": "unresolved", "attemptedAddress": address, "reason": resolved.get("status"), "attemptedAt": now}})
            results.append({"parcel": label, "status": "unresolved", "address": address, "reason": resolved.get("status")})

    if changed:
        update_payload: Dict[str, Any] = {}
        if isinstance(project.get("land_acquisition"), list):
            update_payload["land_acquisition"] = project["land_acquisition"]
        ledger = project.get("landLedger")
        if isinstance(ledger, dict) and isinstance(ledger.get("parcels"), list):
            update_payload["landLedger"] = ledger
        if update_payload:
            project_repository.update(project_id, update_payload)

    return {
        "schemaVersion": "realytics-parcel-pnu-resolution-v1",
        "projectId": project_id,
        "addressPrefix": prefix or None,
        "resolvedCount": resolved_count,
        "attemptedCount": attempted,
        "totalParcels": len(_grouped_parcels(project)),
        "results": results,
    }
