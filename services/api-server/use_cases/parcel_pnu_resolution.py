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
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# Re-exported for callers that treat this module as the parcel-resolution API.
from use_cases.intake_finalize import resolve_address_to_parcel  # noqa: F401

MAX_ADDRESS_CHARS = 2048
MAX_ADDRESS_TOKEN_CHARS = 128


def _address_prefix(land_address: str) -> str:
    if not isinstance(land_address, str) or len(land_address) > MAX_ADDRESS_CHARS:
        return ""
    tokens = land_address.strip().split()
    locality_end = 0
    for index, token in enumerate(tokens):
        if len(token) > MAX_ADDRESS_TOKEN_CHARS:
            return ""
        lot = token.removesuffix("번지").removeprefix("산")
        parts = lot.split("-", 1)
        if all(part and part.isascii() and part.isdecimal() for part in parts):
            break
        if (
            len(token) > 1 and token[-1] in "동읍면리가"
            and all("가" <= char <= "힣" or "0" <= char <= "9" for char in token[:-1])
        ):
            locality_end = index + 1
    return " ".join(tokens[:locality_end])

def _dong_of(prefix: str) -> str:
    tokens = prefix.split()
    return tokens[-1] if tokens else ""


def _lot_key(value: Any) -> tuple | None:
    """Normalize a 지번 ("13", "13-2", "산13", "13대") to (san, main, sub) for comparison."""
    text = str(value or "")
    main = re.search(r"\d+", text)
    if not main:
        return None
    mountain = text[:main.start()].rstrip().endswith("산")
    suffix = text[main.end():]
    sub = re.match(r"\d+", suffix[1:]) if suffix.startswith("-") else None
    return (mountain, int(main[0]), int(sub[0]) if sub else 0)


def _resolved_lot_matches(requested: str, resolved_lot: Any) -> bool | None:
    """True/False when both lots are comparable; None when unverifiable."""
    want = _lot_key(requested)
    got = _lot_key(resolved_lot)
    if want is None or got is None:
        return None
    return want == got


def _parcel_lot(parcel: Dict[str, Any]) -> str:
    lot = (
        parcel.get("normalizedLotNumber")
        or parcel.get("lotNumber")
        or parcel.get("rawLotNumber")
    )
    if lot:
        return str(lot).strip()
    main = parcel.get("mainLotNo", parcel.get("main_lot_no"))
    if main is None or main == "":
        return ""
    sub = parcel.get("subLotNo", parcel.get("sub_lot_no"))
    mountain = parcel.get("isMountain") is True or parcel.get("is_mountain") is True
    return f"{'산' if mountain else ''}{main}{f'-{sub}' if sub not in (None, '', 0, '0') else ''}"


def _has_municipality(address: str) -> bool:
    return any(token.endswith(("시", "구", "군")) for token in address.split())


def _locality_tokens(address: str) -> tuple[str, ...]:
    tokens = _address_prefix(address).split()
    if tokens:
        tokens[0] = re.sub(r"(특별자치시|특별자치도|특별시|광역시)$", "", tokens[0])
    return tuple(tokens)


def _candidate_address(parcel: Dict[str, Any], prefix: str) -> str:
    """Full address for one parcel: project prefix + parcel dong/lot fragment."""
    fragment = str(parcel.get("addressFragment") or "").strip()
    lot = _parcel_lot(parcel)
    dong = str(parcel.get("dong") or "").strip()

    # If the fragment already carries a 시/구, it is a full-enough address.
    if fragment and _has_municipality(fragment):
        return fragment
    if not prefix:
        return fragment or " ".join(p for p in [dong, lot] if p)

    dong = dong or _dong_of(_address_prefix(fragment))
    prefix_dong = _dong_of(prefix)
    if dong and prefix_dong and dong != prefix_dong:
        # Parcel belongs to a different dong: swap the prefix's dong.
        base = prefix.rsplit(" ", 1)[0] if " " in prefix else ""
        return " ".join(p for p in [base, dong, lot] if p)
    if lot:
        return f"{prefix} {lot}"
    return fragment and f"{prefix.rsplit(' ', 1)[0]} {fragment}" or prefix


def _iter_parcel_lists(project: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    lists = []
    land = project.get("land")
    land = land if isinstance(land, dict) else {}
    for container in (
        project.get("landLedger"),
        project.get("land_ledger"),
        project.get("land_acquisition"),
        land.get("parcels"),
        land.get("landLedger"),
    ):
        if isinstance(container, dict):
            container = next(
                (container[key] for key in ("parcels", "rows", "landLedger", "items") if isinstance(container.get(key), list)),
                None,
            )
        if isinstance(container, list):
            lists.append(container)
    stored_sources = project.get("landLedgerSources")
    if isinstance(stored_sources, dict):
        lists.extend(rows for rows in stored_sources.values() if isinstance(rows, list))
    return lists


def retain_land_ledger_sources(
    project: Dict[str, Any], ledger: Dict[str, Any], source_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Retain parcel identities and exact row locations from each uploaded ledger."""
    existing = project.get("landLedgerSources")
    sources = dict(existing) if isinstance(existing, dict) else {}
    previous = project.get("landLedger") or project.get("land_ledger") or {}
    previous_import = previous.get("importMetadata") or {} if isinstance(previous, dict) else {}
    previous_id = previous_import.get("originalFileHash") if isinstance(previous_import, dict) else None
    for document_id, document in ((previous_id, previous), (source_id, ledger)):
        # A repeated archive hash identifies the same original rows. Keep
        # any PNU already verified for them during an earlier review.
        if not document_id or document_id in sources or not isinstance(document, dict):
            continue
        rows = []
        for row in document.get("parcels") or []:
            if not isinstance(row, dict):
                continue
            source = row.get("source") or {}
            locator = {
                "sourceType": "excel_row",
                "sourceDocumentId": document_id,
                "sheetName": document.get("sheetName"),
                "rowNumber": source.get("row") if isinstance(source, dict) else None,
            }
            rows.append({
                **{key: row[key] for key in (
                    "dong", "lotNumber", "normalizedLotNumber", "addressFragment", "isMountain",
                    "mainLotNo", "subLotNo", "pnu", "resolvedPnu", "pnuMatchMethod", "pnuResolution",
                    "excludedFromProject", "includedInProject",
                ) if key in row},
                "parcelSourceLocators": [locator],
            })
        sources[document_id] = rows
    return sources


def _pnu_lot(pnu: Any) -> tuple | None:
    value = str(pnu or "").strip()
    if not re.fullmatch(r"[0-9]{10}[12][0-9]{8}", value):
        return None
    return (value[10] == "2", int(value[11:15]), int(value[15:19]))


def parcel_pnu(parcel: Dict[str, Any]) -> str:
    """Return a usable cadastral key, excluding explicit conflicts."""
    resolution = parcel.get("pnuResolution")
    if isinstance(resolution, dict) and resolution.get("reason") == "conflicting_existing_pnu":
        return ""
    if parcel.get("pnuMatchMethod") == "legal_dong_code_and_normalized_lot":
        return ""
    candidates = {str(parcel[key]).strip() for key in ("pnu", "resolvedPnu", "PNU") if parcel.get(key)}
    if len(candidates) != 1:
        return ""
    pnu = next(iter(candidates))
    pnu_lot = _pnu_lot(pnu)
    lot = _lot_key(_parcel_lot(parcel))
    return pnu if pnu_lot and (lot is None or pnu_lot == lot) else ""


def _grouped_parcels(project: Dict[str, Any]) -> "List[List[Dict[str, Any]]]":
    """Group every parcel dict (across both lists) by identity, preserving order.

    Returns a list of groups; each group is the set of dict objects that refer to
    the same physical parcel, so resolving once can be written back to all of them.
    """
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    prefix = _project_prefix(project)
    addresses_by_pnu = {}
    rows = []
    for parcels in _iter_parcel_lists(project):
        for parcel in parcels:
            if not isinstance(parcel, dict):
                continue
            address = _candidate_address(parcel, prefix)
            locality = _locality_tokens(address)
            lot = _lot_key(_parcel_lot(parcel))
            address_key = ("lot", locality, lot) if locality and lot is not None else None
            pnu = parcel_pnu(parcel)
            if address_key and pnu:
                addresses_by_pnu.setdefault(pnu, set()).add(address_key)
            rows.append((parcel, address_key, pnu))
    for parcel, address_key, pnu in rows:
        known_addresses = addresses_by_pnu.get(pnu, set())
        if requested_pnu := parcel.get("requestedPnu"):
            key = ("requested_pnu", requested_pnu)
        elif address_key:
            key = address_key
        elif len(known_addresses) == 1:
            key = next(iter(known_addresses))
        else:
            key = ("pnu", pnu) if pnu else ("row", id(parcel))
        groups.setdefault(key, []).append(parcel)
    return list(groups.values())


def _project_prefix(project: Dict[str, Any]) -> str:
    land = project.get("land")
    return _address_prefix(str(land.get("address") or "")) if isinstance(land, dict) else ""


def _stored_group_pnus(group: List[Dict[str, Any]]) -> set[str]:
    return {
        str(parcel[key]).strip()
        for parcel in group
        for key in ("pnu", "resolvedPnu", "PNU")
        if parcel.get(key)
    } - {""}


def canonical_project_parcel_rows(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Merge stored copies of the same parcel for downstream reviews."""
    rows = []
    groups = _grouped_parcels(project)
    group_pnu_counts = Counter(pnu for group in groups for pnu in _stored_group_pnus(group))
    for group in groups:
        row = {}
        stored = _stored_group_pnus(group)
        pnus = {pnu for parcel in group if (pnu := parcel_pnu(parcel))}
        for parcel in reversed(group):
            row.update({key: value for key, value in parcel.items() if value not in (None, "", [], {})})
        locators = []
        for parcel in group:
            for locator in parcel.get("parcelSourceLocators") or []:
                if isinstance(locator, dict) and locator not in locators:
                    locators.append(locator)
        if locators:
            row["parcelSourceLocators"] = locators
        if len(stored) > 1 or any(group_pnu_counts[pnu] > 1 for pnu in stored):
            row["pnuResolution"] = {"status": "unresolved", "reason": "conflicting_existing_pnu"}
        elif len(pnus) == 1 and not parcel_pnu(row):
            row["pnu"] = next(iter(pnus))
        rows.append(row)
    return rows


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
        **({"cadastralAddress": props["addr"]} if props.get("addr") else {}),
    }


def parcel_lookup_address(parcel: Dict[str, Any], project_address: str) -> str:
    address = _candidate_address(parcel, _address_prefix(project_address))
    return address if _has_municipality(_address_prefix(address)) and _lot_key(_parcel_lot(parcel)) else ""


def parcel_address_lookup_mismatch(parcel: Dict[str, Any], address: str, resolved: Dict[str, Any]) -> str | None:
    """Reject unrelated cadastral rows returned by approximate point searches."""
    if resolved.get("status") != "resolved":
        return resolved.get("status") or "lookup_failed"
    returned_lot = _pnu_lot(resolved.get("pnu"))
    if returned_lot is None:
        return "invalid_resolved_pnu"
    lot = _parcel_lot(parcel)
    if returned_lot != _lot_key(lot) or _resolved_lot_matches(lot, resolved.get("lotNumber")) is False:
        return f"lot_mismatch(resolved 지번={resolved.get('lotNumber')})"
    returned_address = str(resolved.get("cadastralAddress", resolved.get("addressFragment")) or "")
    if _has_municipality(_address_prefix(returned_address)):
        requested, returned = _locality_tokens(address), _locality_tokens(returned_address)
        common_length = min(len(requested), len(returned))
        if requested[-common_length:] != returned[-common_length:]:
            return "locality_mismatch"
    elif "cadastralAddress" in resolved:
        return "locality_unverified"
    return None


def _verified_pnu_patch(
    result: Dict[str, Any], *, method: str, source: str, now: str, **locator: Any
) -> Dict[str, Any]:
    pnu = str(result["pnu"]).strip()
    patch: Dict[str, Any] = {
        "pnu": pnu,
        "resolvedPnu": pnu,
        "pnuResolveConfidence": 1.0,
        "pnuMatchMethod": method,
        "pnuResolution": {
            "status": "resolved",
            "source": source,
            "resolvedAt": now,
            "lotVerification": "verified",
            **locator,
        },
    }
    if result.get("officialLandPricePerSqm"):
        patch.update({
            "officialLandPricePerSqm": result["officialLandPricePerSqm"],
            "officialLandPriceBaseYear": result.get("officialLandPriceBaseYear"),
            "officialLandPriceSource": source,
        })
    return patch


def resolve_project_parcels_use_case(
    project_repository,
    project_id: str,
    *,
    resolver: Optional[Callable[[str], Dict[str, Any]]] = None,
    dong_code_resolver: Optional[Callable[[str], Optional[str]]] = None,
    pnu_lookup: Optional[Callable[[str], Dict[str, Any]]] = None,
    limit: int = 30,
) -> Dict[str, Any]:
    project = project_repository.get(project_id)
    if not project:
        raise LookupError("Project not found")
    result, updates = resolve_parcel_rows(
        project, project_id, resolver=resolver, dong_code_resolver=dong_code_resolver,
        pnu_lookup=pnu_lookup, limit=limit,
    )
    if updates:
        project_repository.update(project_id, updates)
    return result


def resolve_parcel_rows(
    project: Dict[str, Any],
    project_id: str,
    *,
    resolver: Optional[Callable[[str], Dict[str, Any]]] = None,
    dong_code_resolver: Optional[Callable[[str], Optional[str]]] = None,
    pnu_lookup: Optional[Callable[[str], Dict[str, Any]]] = None,
    limit: int = 30,
    parcel_indexes: set[int] | None = None,
    timeout_seconds: float = 8.0,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Resolve a PNU + 공시지가 for every parcel that lacks one (up to ``limit``).

    Primary (precise) strategy: geocode the 동 once to obtain its 10-digit
    법정동코드, then construct each parcel's PNU from its 지번 and verify it against
    VWorld's cadastre — no geocode→point→point-in-polygon ambiguity, so a wrong
    neighbor parcel is never attached. A 지번 that isn't in the current cadastre
    (e.g. merged/renumbered in redevelopment) stays honestly unresolved.

    Legacy fallback: when a caller injects only ``resolver`` (address→parcel), or
    when the 법정동코드 cannot be determined, the per-address resolver is used with
    the lot-match guard.

    Mutates the supplied parcel dicts and returns the summary and changed data.
    The caller may supply a detached project copy for an unsaved preview.
    """
    # Legacy mode preserves the address-resolver path for callers/tests that
    # inject only `resolver` (no construction tools) — keeps them offline & stable.
    legacy_mode = resolver is not None and dong_code_resolver is None and pnu_lookup is None
    if resolver is None:
        from use_cases.intake_finalize import resolve_address_to_parcel

        def resolver(addr: str) -> Dict[str, Any]:
            if parcel_indexes is not None:
                return resolve_address_to_parcel(addr, timeout=timeout_seconds, max_address_candidates=1)
            return resolve_address_to_parcel(addr)
    if not legacy_mode:
        from integrations.vworld import fetch_parcel_by_pnu, resolve_legal_dong_code

        dong_code_resolver = dong_code_resolver or (
            lambda addr: resolve_legal_dong_code(addr, **({} if timeout_seconds == 8.0 else {"timeout": timeout_seconds}))
        )
        if pnu_lookup is None:
            def pnu_lookup(pnu: str) -> Dict[str, Any]:
                props, diag = fetch_parcel_by_pnu(pnu, **({} if timeout_seconds == 8.0 else {"timeout": timeout_seconds}))
                if props and diag.get("status") == "resolved":
                    return _pnu_lookup_to_result(props)
                return {"status": diag.get("status", "not_found")}

    prefix = _project_prefix(project)
    groups = _grouped_parcels(project)
    group_pnu_counts = Counter(pnu for group in groups for pnu in _stored_group_pnus(group))
    now = datetime.now(timezone.utc).isoformat()

    # Determine the 법정동코드 once (construction mode only).
    legal_dong_code: Optional[str] = None
    if not legacy_mode and prefix and any(
        not any(parcel_pnu(parcel) for parcel in group)
        for index, group in enumerate(groups, start=1)
        if parcel_indexes is None or index in parcel_indexes
    ):
        # Geocode the dong prefix; append the first parcel lot to help VWorld parse.
        first_lot = ""
        for index, group in enumerate(groups, start=1):
            if parcel_indexes is not None and index not in parcel_indexes:
                continue
            head = group[0]
            if _address_prefix(_candidate_address(head, prefix)) == prefix and _parcel_lot(head):
                first_lot = _parcel_lot(head)
                break
        probe = f"{prefix} {first_lot}".strip() if first_lot else prefix
        legal_dong_code = dong_code_resolver(probe) or dong_code_resolver(prefix)
    results: List[Dict[str, Any]] = []
    resolved_count = 0
    attempted = 0
    changed = False

    def _apply(group: List[Dict[str, Any]], patch: Dict[str, Any]) -> None:
        for member in group:
            member.update(patch)

    for index, group in enumerate(groups, start=1):
        head = group[0]
        label = head.get("addressFragment") or _parcel_lot(head)
        if parcel_indexes is not None and index not in parcel_indexes:
            results.append({"parcel": label, "status": "skipped_limit"})
            continue
        existing = _stored_group_pnus(group)
        if len(existing) > 1 or any(group_pnu_counts[pnu] > 1 for pnu in existing):
            reason = "conflicting_existing_pnu"
            _apply(group, {"pnuResolution": {"status": "unresolved", "reason": reason}})
            changed = True
            results.append({"parcel": label, "status": "unresolved", "reason": reason})
            continue
        verified = next((p for p in group if parcel_pnu(p)), None)
        if verified:
            pnu = parcel_pnu(verified)
            if verified and _lot_key(_parcel_lot(head)) == _pnu_lot(pnu):
                for member in group:
                    if not parcel_pnu(member):
                        member["pnu"] = pnu
                        for field in ("pnuResolveConfidence", "pnuMatchMethod", "pnuResolution"):
                            if verified.get(field) is not None:
                                member[field] = verified[field]
                        changed = True
            results.append({"parcel": label, "status": "already_resolved", "pnu": pnu})
            continue
        if attempted >= limit:
            results.append({"parcel": label, "status": "skipped_limit"})
            continue

        # --- Primary: precise PNU construction from the 법정동코드 + 지번 ---------
        address = parcel_lookup_address(head, prefix)
        if not address:
            results.append({"parcel": label, "status": "no_address"})
            continue
        # A same-named dong in another municipality is a different jurisdiction.
        same_locality = _locality_tokens(address) == _locality_tokens(prefix)
        if not legacy_mode and legal_dong_code and same_locality:
            from integrations.vworld import build_pnu

            constructed = build_pnu(legal_dong_code, _parcel_lot(head))
            if constructed:
                attempted += 1
                changed = True
                looked_up = pnu_lookup(constructed)
                lookup_status = looked_up.get("status")
                if (
                    lookup_status == "resolved"
                    and str(looked_up.get("pnu") or "").strip() == constructed
                    and not parcel_address_lookup_mismatch(head, address, looked_up)
                ):
                    patch = _verified_pnu_patch(
                        looked_up, method="pnu_construction",
                        source="VWorld 지적도 PNU 직접조회 (법정동코드+지번)",
                        now=now, constructedPnu=constructed, resolvedAddress=address,
                    )
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
                    reason = {
                        "not_found": "not_in_current_cadastre",
                        "resolved": "pnu_lookup_mismatch",
                    }.get(lookup_status, lookup_status or "lookup_failed")
                    _apply(group, {"pnuResolution": {"status": "unresolved", "constructedPnu": constructed, "reason": reason, "attemptedAt": now}})
                    results.append({"parcel": label, "status": "unresolved", "constructedPnu": constructed, "reason": reason, "method": "pnu_construction"})
                continue
            # build_pnu failed (malformed 지번) → fall through to address resolver.

        # --- Fallback: address geocode → point → parcel (with lot-match guard) ----
        attempted += 1
        resolved = resolver(address)
        # Geocoders can silently fall back to dong-level coordinates and return a
        # NEIGHBORING parcel — attaching a wrong PNU to lender data is fabrication.
        # Check both the PNU-encoded lot and the reported cadastral lot.
        changed = True
        reason = parcel_address_lookup_mismatch(head, address, resolved)
        if not reason and same_locality and legal_dong_code and str(resolved["pnu"]).strip()[:10] != legal_dong_code:
            reason = "locality_mismatch"
        if reason:
            _apply(group, {"pnuResolution": {"status": "unresolved", "attemptedAddress": address, "reason": reason, "attemptedAt": now}})
            results.append({"parcel": label, "status": "unresolved", "address": address, "reason": reason})
        else:
            patch = _verified_pnu_patch(
                resolved, method="address_geocode", source="VWorld 연속지적도 (공공 API)",
                now=now, resolvedAddress=address,
            )
            _apply(group, patch)
            resolved_count += 1
            results.append({
                "parcel": label,
                "status": "resolved",
                "address": address,
                "pnu": patch["pnu"],
                "officialLandPricePerSqm": resolved.get("officialLandPricePerSqm"),
            })

    updates = {
        key: project[key]
        for key in ("landLedger", "land_ledger", "land_acquisition", "land", "landLedgerSources")
        if key in project
    } if changed else {}
    return {
        "schemaVersion": "realytics-parcel-pnu-resolution-v1",
        "projectId": project_id,
        "addressPrefix": prefix or None,
        "resolvedCount": resolved_count,
        "attemptedCount": attempted,
        "totalParcels": len(groups),
        "results": results,
    }, updates
