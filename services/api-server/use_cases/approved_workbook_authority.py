from __future__ import annotations

"""Authority checks for approved workbook data used by runtime calculations."""

import re
from typing import Any, Mapping


GOVERNED_WORKBOOK_CORE_FIELDS = (
    "program.gross_floor_area_sqm",
    "revenue.total_sales_revenue",
    "cost.total_project_cost",
    "financing.pf_amount",
)
ACCEPTABLE_WORKBOOK_BRIDGE_STATUSES = frozenset(
    {"approved", "already_bridged"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def has_authoritative_workbook_snapshot_binding(
    canonical_study: Mapping[str, Any],
) -> bool:
    """Return whether a canonical workbook is bound to a complete Parser V2 snapshot."""

    raw_import = canonical_study.get("raw_import")
    input_references = canonical_study.get("input_references")
    if not isinstance(raw_import, Mapping) or not isinstance(
        input_references, Mapping
    ):
        return False
    if str(raw_import.get("canonicalStatus") or "").casefold() != "approved":
        return False

    bridge = raw_import.get("parserV2Bridge")
    if not isinstance(bridge, Mapping):
        return False
    if (
        str(bridge.get("status") or "").casefold()
        not in ACCEPTABLE_WORKBOOK_BRIDGE_STATUSES
    ):
        return False

    snapshot_id = str(
        input_references.get("approved_input_snapshot_id") or ""
    ).strip()
    snapshot_hash = str(
        input_references.get("approved_input_snapshot_hash") or ""
    ).strip().casefold()
    bridge_snapshot_id = str(
        bridge.get("approvedInputSnapshotId") or ""
    ).strip()
    bridge_snapshot_hash = str(
        bridge.get("approvedInputSnapshotHash") or ""
    ).strip().casefold()
    if not snapshot_id or not _SHA256.fullmatch(snapshot_hash):
        return False
    if (
        snapshot_id != bridge_snapshot_id
        or snapshot_hash != bridge_snapshot_hash
    ):
        return False

    approved_fields = bridge.get("approvedFields")
    if not isinstance(approved_fields, (list, tuple)):
        return False
    normalized_fields = [
        str(field).strip()
        for field in approved_fields
        if str(field).strip()
    ]
    return (
        len(normalized_fields) == len(GOVERNED_WORKBOOK_CORE_FIELDS)
        and set(normalized_fields) == set(GOVERNED_WORKBOOK_CORE_FIELDS)
    )
