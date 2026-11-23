from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from parsing_platform.contracts import ApprovedInputSnapshot
from use_cases.parser_v2_records import (
    ParserV2RecordStore,
    approved_input_snapshot_wire_payload,
)
from use_cases.workbook_parser_v2_bridge import (
    WORKBOOK_BRIDGE_ACTOR,
    WORKBOOK_BRIDGE_PURPOSE,
    WORKBOOK_SNAPSHOT_GATE_ACTOR,
    WorkbookParserV2BridgeBlocked,
    WorkbookParserV2BridgeError,
    bridge_approved_workbook_to_parser_v2,
)


TENANT = "tenant-workbook"
PROJECT = "project-workbook"
REVIEWER = "human-reviewer-1"


class _TickClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 18, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


class _MemoryRecords:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, dict]] = {}
        self.upsert_count = 0

    def upsert(self, project_id: str, record: dict) -> dict:
        self.upsert_count += 1
        saved = copy.deepcopy(record)
        saved.setdefault("projectId", project_id)
        saved.setdefault("updatedAt", saved.get("createdAt"))
        self.rows.setdefault(project_id, {})[saved["recordId"]] = saved
        return copy.deepcopy(saved)

    def list(self, project_id: str, **filters) -> list[dict]:
        records = list(self.rows.get(project_id, {}).values())
        record_type = filters.get("record_type")
        if record_type:
            records = [
                item for item in records if item.get("recordType") == record_type
            ]
        return copy.deepcopy(records[: int(filters.get("limit") or 1000)])


def _store() -> tuple[ParserV2RecordStore, _MemoryRecords]:
    backend = _MemoryRecords()
    return (
        ParserV2RecordStore(
            upsert_fn=backend.upsert,
            list_fn=backend.list,
            clock=_TickClock(),
        ),
        backend,
    )


def _source(source_hash: str = "a" * 64) -> dict:
    return {
        "archiveId": source_hash,
        "originalFileHash": source_hash,
        "archiveStored": True,
        "archiveIntegrityStatus": "verified",
        "archiveObjectKey": f"{PROJECT}/{source_hash}.xlsx",
        "documentType": "feasibility_workbook",
        "validationStatus": "passed",
        "originalFileName": "approved-feasibility.xlsx",
        "contentType": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        "fileSizeBytes": 12345,
    }


def _basis() -> dict:
    return {
        "revenue.total_sales_revenue": {
            "vatTreatment": "included",
            "amountScope": "gross",
            "allocationBasis": "direct",
            "currency": "KRW",
        },
        "cost.total_project_cost": {
            "vatTreatment": "included",
            "amountScope": "gross",
            "allocationBasis": "mixed",
            "allocationMethod": "approved_workbook_total",
            "currency": "KRW",
        },
        "financing.pf_amount": {
            "vatTreatment": "not_applicable",
            "amountScope": "not_applicable",
            "allocationBasis": "not_applicable",
            "financingBalanceBasis": "commitment",
            "currency": "KRW",
        },
    }


def _study(
    source_hash: str = "a" * 64,
    *,
    import_id: str = "import-1",
    revenue: float = 150_000_000_000,
    total_cost: float = 120_000_000_000,
) -> dict:
    return {
        "project_id": PROJECT,
        "project_name": "Approved workbook bridge",
        "asset_type": "mixed_use",
        "program": {
            "gross_floor_area_sqm": {
                "key": "gross_floor_area_sqm",
                "label": "Gross floor area",
                "value": 45_000,
                "unit": "SQM",
                "provenance": {
                    "source_document": "approved-feasibility.xlsx",
                    "source_type": "xlsx",
                    "sheet_name": "Inputs",
                    "cell": "D15",
                    "confidence": 1.0,
                },
            }
        },
        "metrics": {
            "total_revenue": revenue,
            "total_cost": total_cost,
        },
        "financing": [
            {
                "tranche_id": "tr-a",
                "name": "Tr.A",
                "priority": "senior",
                "amount": 65_000_000_000,
            },
            {
                "tranche_id": "equity",
                "name": "Equity",
                "priority": "equity",
                "amount": 10_000_000_000,
            },
        ],
        "raw_import": {
            "canonicalStatus": "approved",
            "sourceDocument": {
                "archiveId": source_hash,
                "originalFileHash": source_hash,
            },
            "mappedCells": {
                "totalRevenue": "Business Summary!I24",
                "totalCost": "Business Summary!I81",
                "pfAmount": "Cash Flow!E48",
            },
            "governedFactBasis": _basis(),
            "candidateImport": {
                "importId": import_id,
                "recordedBy": "workbook-preparer-1",
                "recordedAt": "2026-07-17T00:00:00Z",
                "status": "reviewed",
                "draftHash": "d" * 64,
                "sourceDocumentHashes": [source_hash],
            },
            "approval": {
                "importId": import_id,
                "candidateImportId": import_id,
                "preparedBy": "workbook-preparer-1",
                "approvedBy": REVIEWER,
                "approvedAt": "2026-07-18T00:00:00Z",
            },
        },
    }


def _bridge(
    store: ParserV2RecordStore,
    *,
    import_id: str = "import-1",
    source_hash: str = "a" * 64,
    study: dict | None = None,
) -> dict:
    return bridge_approved_workbook_to_parser_v2(
        record_store=store,
        tenant_id=TENANT,
        project_id=PROJECT,
        import_id=import_id,
        canonical_study=study or _study(source_hash),
        source_document=_source(source_hash),
        approved_by=REVIEWER,
    )


def test_bridge_creates_governed_chain_with_exact_workbook_lineage() -> None:
    store, _backend = _store()

    result = _bridge(store)

    assert result["status"] == "approved"
    assert result["approvedFields"] == [
        "program.gross_floor_area_sqm",
        "revenue.total_sales_revenue",
        "cost.total_project_cost",
        "financing.pf_amount",
    ]
    assert result["missingFields"] == []
    snapshot = result["snapshot"]
    ApprovedInputSnapshot.model_validate(
        approved_input_snapshot_wire_payload(snapshot)
    )
    assert snapshot["purpose"] == WORKBOOK_BRIDGE_PURPOSE
    assert snapshot["approvedBy"] == WORKBOOK_SNAPSHOT_GATE_ACTOR
    assert snapshot["inputs"] == {
        "program.gross_floor_area_sqm": 45_000.0,
        "revenue.total_sales_revenue": 150_000_000_000.0,
        "cost.total_project_cost": 120_000_000_000.0,
        "financing.pf_amount": 65_000_000_000.0,
    }
    assert {item["sourceHash"] for item in snapshot["inputItems"]} == {"a" * 64}
    assert {
        (item["sourceLocator"]["sheetName"], item["sourceLocator"].get("cellReference")
         or item["sourceLocator"].get("cellRange"))
        for item in snapshot["inputItems"]
    } == {
        ("Inputs", "D15"),
        ("Business Summary", "I24"),
        ("Business Summary", "I81"),
        ("Cash Flow", "E48"),
    }

    run = store.get_parsing_run(TENANT, PROJECT, result["parsingRunId"])
    assert run["actorId"] == WORKBOOK_BRIDGE_ACTOR
    assert run["executionStatus"] == "completed"
    assert [
        item["toExecutionStatus"] for item in run["transitionEvents"]
    ] == ["running", "completed"]
    assert {
        item["reviewerId"]
        for item in store.list_review_decisions(TENANT, PROJECT)
    } == {REVIEWER}
    assert all(
        candidate["metadata"]["basis"]["currency"] == "KRW"
        for candidate in store.list_candidates(TENANT, PROJECT)
        if candidate["canonicalField"] != "program.gross_floor_area_sqm"
    )


def test_bridge_replay_is_idempotent() -> None:
    store, backend = _store()
    first = _bridge(store)
    write_count = backend.upsert_count

    replay = _bridge(store)

    assert replay["status"] == "already_bridged"
    assert replay["snapshot"]["snapshotId"] == first["snapshot"]["snapshotId"]
    assert backend.upsert_count == write_count


def test_bridge_retry_after_mid_review_failure_reuses_existing_decisions(
    monkeypatch,
) -> None:
    store, _backend = _store()
    original_create_selection = store.create_canonical_selection
    call_count = 0

    def fail_during_second_selection(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated transient persistence failure")
        return original_create_selection(**kwargs)

    monkeypatch.setattr(
        store,
        "create_canonical_selection",
        fail_during_second_selection,
    )
    with pytest.raises(RuntimeError, match="simulated transient"):
        _bridge(store)

    monkeypatch.setattr(
        store,
        "create_canonical_selection",
        original_create_selection,
    )
    result = _bridge(store)

    assert result["status"] == "approved"
    decisions = store.list_review_decisions(TENANT, PROJECT)
    assert len(decisions) == 4
    assert len({item["reviewDecisionId"] for item in decisions}) == 4
    assert len(
        store.list_canonical_selections(TENANT, PROJECT, active_only=True)
    ) == 4


def test_new_approved_import_supersedes_snapshot_and_field_selections() -> None:
    store, _backend = _store()
    first = _bridge(store)

    second = _bridge(
        store,
        import_id="import-2",
        source_hash="b" * 64,
        study=_study(
            "b" * 64,
            import_id="import-2",
            revenue=160_000_000_000,
            total_cost=125_000_000_000,
        ),
    )

    assert second["status"] == "approved"
    assert (
        second["snapshot"]["supersedesSnapshotId"]
        == first["snapshot"]["snapshotId"]
    )
    active = store.list_approved_input_snapshots(
        TENANT, PROJECT, active_only=True
    )
    assert [item["snapshotId"] for item in active] == [
        second["snapshot"]["snapshotId"]
    ]
    historical = {
        item["snapshotId"]: item
        for item in store.list_approved_input_snapshots(TENANT, PROJECT)
    }
    assert historical[first["snapshot"]["snapshotId"]]["validityStatus"] == (
        "invalidated"
    )
    assert historical[first["snapshot"]["snapshotId"]]["approvalStatus"] == (
        "superseded"
    )
    assert {
        item["selectionVersion"]
        for item in store.list_canonical_selections(
            TENANT, PROJECT, active_only=True
        )
    } == {2}


def test_superseded_workbook_snapshot_cannot_be_replayed() -> None:
    store, backend = _store()
    first = _bridge(store)
    _bridge(
        store,
        import_id="import-2",
        source_hash="b" * 64,
        study=_study(
            "b" * 64,
            import_id="import-2",
            revenue=160_000_000_000,
            total_cost=125_000_000_000,
        ),
    )
    write_count = backend.upsert_count

    with pytest.raises(
        WorkbookParserV2BridgeError,
        match="superseded or invalidated",
    ):
        _bridge(store)

    assert first["snapshot"]["approvalStatus"] == "approved"
    historical = {
        item["snapshotId"]: item
        for item in store.list_approved_input_snapshots(TENANT, PROJECT)
    }
    assert historical[first["snapshot"]["snapshotId"]]["approvalStatus"] == (
        "superseded"
    )
    assert historical[first["snapshot"]["snapshotId"]]["validityStatus"] == (
        "invalidated"
    )
    assert backend.upsert_count == write_count


def test_missing_core_value_blocks_before_any_record_is_written() -> None:
    store, backend = _store()
    study = _study()
    study["program"] = {}

    with pytest.raises(
        WorkbookParserV2BridgeBlocked,
        match="all four governed workbook core facts",
    ) as raised:
        _bridge(store, study=study)

    assert backend.upsert_count == 0
    assert {
        (item["fieldKey"], item["status"])
        for item in raised.value.validation_issues
    } == {("program.gross_floor_area_sqm", "missing_value")}


def test_present_core_fact_without_exact_locator_blocks_before_persistence() -> None:
    store, backend = _store()
    study = _study()
    del study["raw_import"]["mappedCells"]["totalCost"]

    with pytest.raises(
        WorkbookParserV2BridgeBlocked,
        match="exact workbook sheet/cell locator",
    ) as raised:
        _bridge(store, study=study)

    assert backend.upsert_count == 0
    assert {
        (item["fieldKey"], item["status"])
        for item in raised.value.validation_issues
    } == {("cost.total_project_cost", "missing_locator")}


def test_missing_monetary_basis_blocks_before_any_record_is_written() -> None:
    store, backend = _store()
    study = _study()
    del study["raw_import"]["governedFactBasis"][
        "revenue.total_sales_revenue"
    ]

    with pytest.raises(
        WorkbookParserV2BridgeBlocked,
        match="basis",
    ) as raised:
        _bridge(store, study=study)

    assert backend.upsert_count == 0
    assert any(
        item["fieldKey"] == "revenue.total_sales_revenue"
        and item["status"] == "blocked_missing_or_invalid_basis"
        for item in raised.value.coverage
    )


def test_registry_blocking_value_fails_closed_before_persistence() -> None:
    store, backend = _store()
    study = _study(total_cost=-1)

    with pytest.raises(
        WorkbookParserV2BridgeBlocked,
        match="field registry",
    ) as raised:
        _bridge(store, study=study)

    assert backend.upsert_count == 0
    assert any(
        item["fieldKey"] == "cost.total_project_cost"
        and item["code"] == "CANONICAL_VALUE_BELOW_MINIMUM"
        for item in raised.value.validation_issues
    )


def test_formula_cached_value_requires_deterministic_recalculation_lineage() -> None:
    store, backend = _store()
    study = _study()
    study["raw_import"]["namedRangeValues"] = {
        "fs_total_revenue": {
            "present": True,
            "destinations": [
                {"sheetName": "Business Summary", "range": "$I$24"}
            ],
            "value": study["metrics"]["total_revenue"],
            "formula": "=SUM(I10:I23)",
        }
    }

    with pytest.raises(
        WorkbookParserV2BridgeBlocked,
        match="deterministic recalculation lineage",
    ) as raised:
        _bridge(store, study=study)

    assert backend.upsert_count == 0
    issue = raised.value.validation_issues[0]
    assert issue["fieldKey"] == "revenue.total_sales_revenue"
    assert issue["status"] == "blocked_formula_recalculation_required"
    assert len(
        issue["sourceLocator"]["metadata"]["workbookFormulaSha256"]
    ) == 64
    assert "workbookFormula" not in issue["sourceLocator"]


def test_multi_tranche_pf_aggregate_requires_derived_fact_lineage() -> None:
    store, backend = _store()
    study = _study()
    study["financing"][0]["amount"] = 50_000_000_000
    study["financing"].insert(
        1,
        {
            "tranche_id": "tr-b",
            "name": "Tr.B",
            "priority": "mezzanine",
            "amount": 15_000_000_000,
        },
    )
    study["raw_import"]["mappedCells"]["pfAmount"] = "Cash Flow!E48:E49"

    with pytest.raises(
        WorkbookParserV2BridgeBlocked,
        match="derived-fact lineage",
    ) as raised:
        _bridge(store, study=study)

    assert backend.upsert_count == 0
    assert {
        (item["fieldKey"], item["status"])
        for item in raised.value.validation_issues
    } == {
        ("financing.pf_amount", "blocked_derived_lineage_required")
    }


def test_profile_calculated_scalar_requires_derived_fact_lineage() -> None:
    store, backend = _store()
    study = _study()
    study["raw_import"]["sampleProfile"] = {
        "templateId": "legacy-profile",
        "fieldLocators": {
            "revenue.total": {
                "sheet": "Business Summary",
                "cell": "I24",
                "kind": "calculated",
            }
        },
    }

    with pytest.raises(
        WorkbookParserV2BridgeBlocked,
        match="derived-fact lineage",
    ) as raised:
        _bridge(store, study=study)

    assert backend.upsert_count == 0
    assert raised.value.validation_issues[0]["fieldKey"] == (
        "revenue.total_sales_revenue"
    )


def test_bridge_rejects_maker_checker_bypass_before_persistence() -> None:
    store, backend = _store()
    study = _study()
    study["raw_import"]["candidateImport"]["recordedBy"] = REVIEWER
    study["raw_import"]["approval"]["preparedBy"] = REVIEWER

    with pytest.raises(
        WorkbookParserV2BridgeError,
        match="maker-checker",
    ):
        _bridge(store, study=study)

    assert backend.upsert_count == 0
