from __future__ import annotations

from copy import deepcopy

import pytest

from use_cases.approved_workbook_authority import (
    GOVERNED_WORKBOOK_CORE_FIELDS,
    has_authoritative_workbook_snapshot_binding,
)


def _bound_study(*, bridge_status: str = "approved") -> dict:
    snapshot_id = "approved-snapshot-1"
    snapshot_hash = "a" * 64
    return {
        "input_references": {
            "approved_input_snapshot_id": snapshot_id,
            "approved_input_snapshot_hash": snapshot_hash,
        },
        "raw_import": {
            "canonicalStatus": "approved",
            "parserV2Bridge": {
                "status": bridge_status,
                "approvedInputSnapshotId": snapshot_id,
                "approvedInputSnapshotHash": snapshot_hash,
                "approvedFields": list(GOVERNED_WORKBOOK_CORE_FIELDS),
            },
        },
    }


@pytest.mark.parametrize("bridge_status", ["approved", "already_bridged"])
def test_complete_matching_snapshot_binding_is_authoritative(
    bridge_status,
) -> None:
    assert has_authoritative_workbook_snapshot_binding(
        _bound_study(bridge_status=bridge_status)
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("raw_import", "canonicalStatus"), "candidate"),
        (("raw_import", "parserV2Bridge", "status"), "blocked"),
        (
            (
                "raw_import",
                "parserV2Bridge",
                "approvedInputSnapshotId",
            ),
            "different-snapshot",
        ),
        (
            (
                "raw_import",
                "parserV2Bridge",
                "approvedInputSnapshotHash",
            ),
            "b" * 64,
        ),
        (
            ("raw_import", "parserV2Bridge", "approvedFields"),
            list(GOVERNED_WORKBOOK_CORE_FIELDS[:-1]),
        ),
    ],
)
def test_incomplete_or_mismatched_snapshot_binding_is_not_authoritative(
    path,
    value,
) -> None:
    study = deepcopy(_bound_study())
    target = study
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert not has_authoritative_workbook_snapshot_binding(study)


def test_snapshot_binding_requires_id_and_sha256_in_input_references() -> None:
    study = _bound_study()
    study["input_references"] = {}

    assert not has_authoritative_workbook_snapshot_binding(study)
