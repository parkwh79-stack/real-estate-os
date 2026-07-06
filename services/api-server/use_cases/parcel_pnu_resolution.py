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


def resolve_project_parcels_use_case(
    project_repository,
    project_id: str,
    *,
    resolver: Optional[Callable[[str], Dict[str, Any]]] = None,
    limit: int = 30,
) -> Dict[str, Any]:
    """Resolve a PNU + 공시지가 for every parcel that lacks one (up to ``limit``).

    Mutates the parcel dicts in place (they live inside the project payload) and
    persists once via the repository. Returns a per-parcel result summary.
    """
    if resolver is None:
        from use_cases.intake_finalize import resolve_address_to_parcel

        resolver = resolve_address_to_parcel

    project = project_repository.get(project_id)
    if not project:
        raise LookupError("Project not found")

    prefix = _address_prefix(str((project.get("land") or {}).get("address") or ""))
    now = datetime.now(timezone.utc).isoformat()

    results: List[Dict[str, Any]] = []
    resolved_count = 0
    attempted = 0
    changed = False

    for parcels in _iter_parcel_lists(project):
        for parcel in parcels:
            if not isinstance(parcel, dict):
                continue
            if parcel.get("pnu"):
                results.append(
                    {
                        "parcel": parcel.get("addressFragment") or _parcel_lot(parcel),
                        "status": "already_resolved",
                        "pnu": parcel.get("pnu"),
                    }
                )
                continue
            if attempted >= limit:
                results.append(
                    {"parcel": parcel.get("addressFragment") or _parcel_lot(parcel), "status": "skipped_limit"}
                )
                continue

            address = _candidate_address(parcel, prefix)
            if not address.strip():
                results.append({"parcel": parcel.get("addressFragment"), "status": "no_address"})
                continue

            attempted += 1
            resolved = resolver(address)
            # Geocoders can silently fall back to dong-level coordinates and
            # return a NEIGHBORING parcel — attaching a wrong PNU to lender data
            # is fabrication. Reject when the resolved 지번 contradicts the
            # requested one; flag "unverified" when it cannot be compared.
            lot_check = (
                _resolved_lot_matches(_parcel_lot(parcel), resolved.get("lotNumber"))
                if resolved.get("status") == "resolved"
                else None
            )
            if resolved.get("status") == "resolved" and lot_check is False:
                parcel["pnuResolution"] = {
                    "status": "unresolved",
                    "attemptedAddress": address,
                    "reason": f"lot_mismatch(resolved 지번={resolved.get('lotNumber')})",
                    "attemptedAt": now,
                }
                changed = True
                results.append(
                    {
                        "parcel": parcel.get("addressFragment") or _parcel_lot(parcel),
                        "status": "unresolved",
                        "address": address,
                        "reason": f"lot_mismatch(resolved 지번={resolved.get('lotNumber')})",
                    }
                )
                continue
            if resolved.get("status") == "resolved":
                parcel["pnu"] = resolved["pnu"]
                if resolved.get("officialLandPricePerSqm"):
                    parcel["officialLandPricePerSqm"] = resolved["officialLandPricePerSqm"]
                    parcel["officialLandPriceBaseYear"] = resolved.get("officialLandPriceBaseYear")
                    parcel["officialLandPriceSource"] = "VWorld 연속지적도 (공공 API)"
                parcel["pnuResolution"] = {
                    "status": "resolved",
                    "resolvedAddress": address,
                    "source": "VWorld 연속지적도 (공공 API)",
                    "resolvedAt": now,
                    "lotVerification": "verified" if lot_check else "unverified",
                }
                resolved_count += 1
                changed = True
                results.append(
                    {
                        "parcel": parcel.get("addressFragment") or _parcel_lot(parcel),
                        "status": "resolved",
                        "address": address,
                        "pnu": resolved["pnu"],
                        "officialLandPricePerSqm": resolved.get("officialLandPricePerSqm"),
                    }
                )
            else:
                parcel["pnuResolution"] = {
                    "status": "unresolved",
                    "attemptedAddress": address,
                    "reason": resolved.get("status"),
                    "attemptedAt": now,
                }
                changed = True
                results.append(
                    {
                        "parcel": parcel.get("addressFragment") or _parcel_lot(parcel),
                        "status": "unresolved",
                        "address": address,
                        "reason": resolved.get("status"),
                    }
                )

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
        "totalParcels": sum(len(lst) for lst in _iter_parcel_lists(project)),
        "results": results,
    }
