import copy
import hashlib
import io

import openpyxl
import pytest

from parsing_platform.adapters import ArchivedDocument
from parsing_platform.contracts import ParsedFactCandidate
from parsing_platform.document_taxonomy import DOCUMENT_TAXONOMY_VERSION
from parsing_platform.feasibility_domain_modules import (
    CANONICAL_WORKBOOK_MAPPING_MODULE_ID,
    CANONICAL_WORKBOOK_MAPPING_VERSION,
    COST_MODULE_ID,
    DOMAIN_MODULE_IDS,
    EXPECTED_ENTITY_POLICY_VERSION,
    FEASIBILITY_DOMAIN_PACK_VERSION,
    PF_TERMS_MODULE_ID,
    PRODUCT_MIX_MODULE_ID,
    RECONCILIATION_MODULE_ID,
    SALES_COLLECTION_MODULE_ID,
    SCHEDULE_MODULE_ID,
    STRUCTURED_FACT_CAPABILITY,
    CostParserModule,
    CrossDocumentReconciliationModule,
    PfTermsParserModule,
    ProductMixParserModule,
    SalesCollectionParserModule,
    ScheduleParserModule,
    canonical_workbook_mapping_hash,
    build_domain_candidate_id,
    build_domain_candidate_content_hash,
    build_feasibility_domain_orchestrator,
    build_structured_fact_lineage,
    feasibility_domain_profile,
)
from parsing_platform.orchestrator import (
    ModuleExecutionResult,
    ParsingOrchestrationResult,
)
from use_cases.parser_v2_candidate_collector import (
    ParserModuleCandidateCollectionError,
    _persist_value_conflicts,
    collect_parser_module_candidates,
    seal_collected_candidate_set,
)
from use_cases.parser_v2_pipeline import run_structural_parsing_use_case
from use_cases.parser_v2_records import (
    ParserV2ApprovalError,
    ParserV2RecordStore,
)


SOURCE_HASH = "a" * 64
MAPPING_ATTESTATION_HASH = "d" * 64
DOCUMENT_VERSION_ID = "document-version:domain-test"
CONTEXT = {
    "tenant_id": "tenant-1",
    "project_id": "project-1",
    "parsing_run_id": "parsing-run:domain-test",
    "document_version_id": DOCUMENT_VERSION_ID,
    "source_hash": SOURCE_HASH,
    "source_authority": "submitted_document",
    "ontology_version": "realytics-real-estate-domain-standard-v1",
    "mapping_attestation_hash": MAPPING_ATTESTATION_HASH,
    "candidate_created_at": "2026-07-18T00:00:00+00:00",
}


class MemoryRecords:
    def __init__(self):
        self.records = {}

    def upsert(self, project_id, record):
        self.records[(project_id, record["recordId"])] = dict(record)
        return dict(record)

    def list(self, project_id, *, record_type=None, limit=1000, **_kwargs):
        rows = [
            dict(record)
            for (stored_project_id, _), record in self.records.items()
            if stored_project_id == project_id
            and (
                record_type is None
                or record.get("recordType") == record_type
            )
        ]
        return rows[:limit]


def _locator(
    *,
    cell: str = "A1",
    source_hash: str = SOURCE_HASH,
    document_version_id: str = DOCUMENT_VERSION_ID,
):
    return {
        "documentVersionId": document_version_id,
        "documentId": "document-1",
        "fileSha256": source_hash,
        "sheetName": "Feasibility",
        "cellReference": cell,
        "sourceType": "excel_cell",
        "evidenceId": f"evidence:{cell}",
        "rawTextSha256": "b" * 64,
        "parserMethod": "xlsx_structure@1.0.0",
    }


def _revenue_cost_basis():
    return {
        "vatTreatment": "excluded",
        "amountScope": "gross",
        "allocationBasis": "direct",
        "currency": "KRW",
    }


def _financing_basis():
    return {
        "vatTreatment": "not_applicable",
        "amountScope": "not_applicable",
        "allocationBasis": "not_applicable",
        "currency": "KRW",
        "financingBalanceBasis": "commitment",
    }


def _payload(row):
    row = dict(row)
    mapping_entry = {
        key: item
        for key, item in row.items()
        if key
        in {
            "domain",
            "fieldKey",
            "entityId",
            "unit",
            "basis",
            "authority",
            "sheetName",
            "cellReference",
        }
    }
    lineage = build_structured_fact_lineage(
        mapping_attestation_hash=MAPPING_ATTESTATION_HASH,
        mapping_entry=mapping_entry,
        value=row.get("rawValue"),
        source_locator=row.get("sourceLocator") or {},
    )
    row["rowId"] = lineage["sourceRowId"]
    row["metadata"] = lineage
    return {
        "input": {
            "documentType": "feasibility_workbook",
            "structuredFactRows": [row],
        },
        "module_results": {},
        "available_capabilities": [
            "document.classified",
            STRUCTURED_FACT_CAPABILITY,
        ],
    }


def _payload_rows(rows, *, canonical_mapping=None):
    structured_rows = [
        _payload(row)["input"]["structuredFactRows"][0] for row in rows
    ]
    root = {
        "documentType": "feasibility_workbook",
        "structuredFactRows": structured_rows,
    }
    if canonical_mapping is not None:
        root["canonicalWorkbookMapping"] = canonical_mapping
    return {
        "input": root,
        "module_results": {},
        "available_capabilities": [
            "document.classified",
            STRUCTURED_FACT_CAPABILITY,
        ],
    }


def _reconcile(*outputs, canonical_mapping=None):
    return CrossDocumentReconciliationModule().execute(
        {
            "input": (
                {"canonicalWorkbookMapping": canonical_mapping}
                if canonical_mapping is not None
                else {}
            ),
            "module_results": {
                output["moduleId"]: {
                    "output": output,
                    "execution_status": "completed",
                }
                for output in outputs
            },
            "available_capabilities": [],
        },
        {},
    )


def _xlsx_document(*, formula: bool = False) -> ArchivedDocument:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Feasibility"
    sheet["A1"] = "=500*2" if formula else 1000
    body = io.BytesIO()
    workbook.save(body)
    workbook.close()
    content = body.getvalue()
    return ArchivedDocument(
        tenant_id="tenant-1",
        project_id="project-1",
        archive_id=hashlib.sha256(content).hexdigest(),
        filename="feasibility.xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        content=content,
    )


def _xlsx_document_cells(values):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Feasibility"
    for cell, value in values.items():
        sheet[cell] = value
    body = io.BytesIO()
    workbook.save(body)
    workbook.close()
    content = body.getvalue()
    return ArchivedDocument(
        tenant_id="tenant-1",
        project_id="project-1",
        archive_id=hashlib.sha256(content).hexdigest(),
        filename="feasibility.xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        content=content,
    )


def _ready_running_store(
    *,
    document_version_id=DOCUMENT_VERSION_ID,
    run_id="parsing-run:collector-test",
):
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    store.create_document_version(
        tenant_id="tenant-1",
        project_id="project-1",
        document_id="document-1",
        document_version_id=document_version_id,
        actor_id="parser-worker-1",
        archive_id="archive-collector-test",
        object_version="object-version-1",
        source_hash=SOURCE_HASH,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        original_file_name="feasibility.xlsx",
        source_authority="submitted_document",
    )
    store.create_parsing_run(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=run_id,
        document_version_id=document_version_id,
        actor_id="parser-worker-1",
        parser_version="parser-v2",
        ontology_version=CONTEXT["ontology_version"],
        prompt_version="deterministic",
        model_version="deterministic",
        profile_id="realytics.feasibility_domain_pack",
        profile_version="1.0.0",
        plan_hash="1" * 64,
        input_hash="2" * 64,
        requested_modules=[*DOMAIN_MODULE_IDS, RECONCILIATION_MODULE_ID],
    )
    store.append_parsing_run_event(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=run_id,
        actor_id="parser-worker-1",
        execution_status="running",
    )
    return store, memory


def _module_execution(module_id, output, *, version="1.0.0"):
    return ModuleExecutionResult(
        module_id=module_id,
        module_version=version,
        stage_id="test",
        execution_status="completed",
        idempotency_key=f"idempotency:{module_id}",
        output=output,
        output_hash="3" * 64,
    )


def _orchestration_result(results, *, status="completed"):
    return ParsingOrchestrationResult(
        execution_status=status,
        idempotency_key="orchestration:test",
        profile_id="realytics.feasibility_domain_pack",
        profile_version="1.0.0",
        results=results,
        trace=(),
        available_capabilities=(),
    )


def _mapping():
    return {
        "schemaVersion": CANONICAL_WORKBOOK_MAPPING_VERSION,
        "documentType": "feasibility_workbook",
        "entries": [
            {
                "rowId": "program-gfa",
                "domain": "product_mix",
                "fieldKey": "program.gross_floor_area_sqm",
                "sheetName": "Feasibility",
                "cellReference": "A1",
                "unit": "sqm",
            }
        ],
    }


def test_domain_pack_manifests_are_versioned_exact_and_independently_composable():
    orchestrator = build_feasibility_domain_orchestrator()
    profile = feasibility_domain_profile(project_id="project-1")
    plan = orchestrator.build_plan(profile)

    assert [item.module_id for item in plan.modules] == [
        "document_inspection",
        "structural_extraction",
        "document_classification",
        CANONICAL_WORKBOOK_MAPPING_MODULE_ID,
        *DOMAIN_MODULE_IDS,
        RECONCILIATION_MODULE_ID,
    ]
    manifests = {
        item.module_id: item for item in orchestrator.registry.list_active()
    }
    for module_id in (
        CANONICAL_WORKBOOK_MAPPING_MODULE_ID,
        *DOMAIN_MODULE_IDS,
        RECONCILIATION_MODULE_ID,
    ):
        manifest = manifests[module_id]
        assert manifest.document_taxonomy_version == DOCUMENT_TAXONOMY_VERSION
        assert manifest.supported_document_types
        assert "*" not in manifest.supported_document_types
        assert manifest.required_locator_precision == "exact"
        assert manifest.metadata.get("authoritative") is False

    product_only = feasibility_domain_profile(
        project_id="project-1",
        domain_module_ids=(PRODUCT_MIX_MODULE_ID,),
        include_intake_core=False,
        include_reconciliation=False,
    )
    product_plan = orchestrator.build_plan(
        product_only,
        base_capabilities=(
            "document.classified",
            STRUCTURED_FACT_CAPABILITY,
        ),
    )
    assert [item.module_id for item in product_plan.modules] == [
        PRODUCT_MIX_MODULE_ID
    ]


@pytest.mark.parametrize(
    ("module", "row", "expected_field"),
    [
        (
            ProductMixParserModule(),
            {
                "rowId": "product-gfa",
                "domain": "product_mix",
                "fieldKey": "program.gross_floor_area_sqm",
                "rawValue": 1000,
                "normalizedValue": 1000,
                "unit": "sqm",
                "sourceLocator": _locator(cell="A1"),
            },
            "program.gross_floor_area_sqm",
        ),
        (
            CostParserModule(),
            {
                "rowId": "cost-total",
                "domain": "cost",
                "fieldKey": "cost.total_project_cost",
                "rawValue": 90_000,
                "normalizedValue": 90_000,
                "unit": "krw",
                "basis": _revenue_cost_basis(),
                "sourceLocator": _locator(cell="B1"),
            },
            "cost.total_project_cost",
        ),
        (
            ScheduleParserModule(),
            {
                "rowId": "schedule-start",
                "domain": "schedule",
                "fieldKey": "schedule.milestone.planned_date",
                "entityId": "milestone:start",
                "rawValue": "2026-07-01",
                "normalizedValue": "2026-07-01",
                "sourceLocator": _locator(cell="C1"),
            },
            "schedule.milestone.planned_date",
        ),
        (
            SalesCollectionParserModule(),
            {
                "rowId": "revenue-total",
                "domain": "sales_collection",
                "fieldKey": "revenue.total_sales_revenue",
                "rawValue": 100_000,
                "normalizedValue": 100_000,
                "unit": "krw",
                "basis": _revenue_cost_basis(),
                "sourceLocator": _locator(cell="D1"),
            },
            "revenue.total_sales_revenue",
        ),
        (
            PfTermsParserModule(),
            {
                "rowId": "pf-total",
                "domain": "pf_terms",
                "fieldKey": "financing.pf_amount",
                "rawValue": 70_000,
                "normalizedValue": 70_000,
                "unit": "krw",
                "basis": _financing_basis(),
                "sourceLocator": _locator(cell="E1"),
            },
            "financing.pf_amount",
        ),
    ],
)
def test_each_domain_module_emits_the_same_strict_candidate_contract(
    module, row, expected_field
):
    output = module.execute(_payload(row), CONTEXT)

    assert output["schemaVersion"] == (
        "realytics-feasibility-domain-module-output-v1"
    )
    assert output["candidateOnly"] is True
    assert output["authoritative"] is False
    assert output["summary"]["candidateCount"] == 1
    candidate = ParsedFactCandidate.model_validate(output["candidates"][0])
    assert candidate.field_key == expected_field
    assert candidate.parser_version == module.manifest.ref
    assert candidate.metadata["domainPackVersion"] == (
        FEASIBILITY_DOMAIN_PACK_VERSION
    )
    assert candidate.candidate_id == build_domain_candidate_id(
        candidate.model_dump(by_alias=True, mode="json", exclude_none=True)
    )
    assert candidate.metadata["candidateContentHash"] == (
        build_domain_candidate_content_hash(
            candidate.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
        )
    )


@pytest.mark.parametrize(
    ("row_patch", "expected_code"),
    [
        ({"unit": None}, "CANONICAL_UNIT_INVALID"),
        ({"entityId": None}, "CANONICAL_ENTITY_ID_REQUIRED"),
        ({"basis": None}, "FACT_BASIS_REQUIRED"),
        (
            {"sourceLocator": _locator(source_hash="c" * 64)},
            "SOURCE_HASH_MISMATCH",
        ),
        (
            {"authority": "official_registry"},
            "SOURCE_AUTHORITY_MISMATCH",
        ),
    ],
)
def test_domain_module_abstains_when_unit_entity_basis_locator_or_authority_is_invalid(
    row_patch, expected_code
):
    row = {
        "rowId": "cost-line",
        "domain": "cost",
        "fieldKey": "cost.line_item.amount",
        "entityId": "cost-line:construction",
        "rawValue": 1000,
        "normalizedValue": 1000,
        "unit": "krw",
        "basis": _revenue_cost_basis(),
        "sourceLocator": _locator(cell="F1"),
    }
    row.update(row_patch)

    output = CostParserModule().execute(_payload(row), CONTEXT)

    assert output["candidates"] == []
    assert expected_code in {
        item["code"] for item in output["diagnostics"]
    }


def test_reconciliation_blocks_incomplete_repeatable_entity():
    product_output = ProductMixParserModule().execute(
        _payload(
            {
                "rowId": "product-type-only",
                "domain": "product_mix",
                "fieldKey": "program.product.asset_type",
                "entityId": "product:apartment",
                "rawValue": "apartment",
                "normalizedValue": "apartment",
                "sourceLocator": _locator(cell="G1"),
            }
        ),
        CONTEXT,
    )
    output = CrossDocumentReconciliationModule().execute(
        {
            "input": {},
            "module_results": {
                PRODUCT_MIX_MODULE_ID: {
                    "output": product_output,
                    "execution_status": "completed",
                }
            },
            "available_capabilities": [],
        },
        {},
    )

    assert "feasibility.reconciliation.clean" not in output[
        "providedCapabilities"
    ]
    assert "ENTITY_FIELD_GROUP_INCOMPLETE" in {
        item["code"] for item in output["diagnostics"]
    }


@pytest.mark.parametrize(
    ("metadata_key", "value", "expected_code"),
    (
        (
            "mappingAttestationHash",
            "c" * 64,
            "MAPPING_ATTESTATION_MISMATCH",
        ),
        (
            "normalizationOutputHash",
            "c" * 64,
            "NORMALIZATION_LINEAGE_HASH_MISMATCH",
        ),
    ),
)
def test_domain_module_abstains_when_mapping_or_normalization_lineage_is_tampered(
    metadata_key,
    value,
    expected_code,
):
    payload = _payload(
        {
            "domain": "product_mix",
            "fieldKey": "program.gross_floor_area_sqm",
            "rawValue": 1000,
            "normalizedValue": 1000,
            "unit": "sqm",
            "sourceLocator": _locator(cell="H1"),
        }
    )
    payload["input"]["structuredFactRows"][0]["metadata"][
        metadata_key
    ] = value

    output = ProductMixParserModule().execute(payload, CONTEXT)

    assert output["candidates"] == []
    assert expected_code in {
        item["code"] for item in output["diagnostics"]
    }


def test_candidate_content_hash_is_repeatable_across_run_and_document_ids():
    first_row = {
        "domain": "product_mix",
        "fieldKey": "program.gross_floor_area_sqm",
        "rawValue": 1000,
        "normalizedValue": 1000,
        "unit": "sqm",
        "sourceLocator": _locator(
            cell="I1",
            document_version_id="document-version:first",
        ),
    }
    second_locator = _locator(
        cell="I1",
        document_version_id="document-version:second",
    )
    second_locator["documentId"] = "document-2"
    second_locator["evidenceId"] = "evidence:second:I1"
    second_row = {
        **first_row,
        "sourceLocator": second_locator,
    }
    first_context = {
        **CONTEXT,
        "parsing_run_id": "parsing-run:first",
        "document_version_id": "document-version:first",
    }
    second_context = {
        **CONTEXT,
        "parsing_run_id": "parsing-run:second",
        "document_version_id": "document-version:second",
    }

    first = ProductMixParserModule().execute(
        _payload(first_row), first_context
    )["candidates"][0]
    second = ProductMixParserModule().execute(
        _payload(second_row), second_context
    )["candidates"][0]

    assert first["metadata"]["normalizationInputHash"] != (
        second["metadata"]["normalizationInputHash"]
    )
    assert first["metadata"]["candidateContentHash"] == (
        second["metadata"]["candidateContentHash"]
    )
    assert first["candidateId"] != second["candidateId"]


def test_candidate_content_hash_includes_temporal_and_calculation_semantics():
    candidate = ProductMixParserModule().execute(
        _payload(
            {
                "domain": "product_mix",
                "fieldKey": "program.gross_floor_area_sqm",
                "rawValue": 1000,
                "normalizedValue": 1000,
                "unit": "sqm",
                "sourceLocator": _locator(cell="I2"),
            }
        ),
        CONTEXT,
    )["candidates"][0]
    baseline = build_domain_candidate_content_hash(candidate)
    mutations = (
        {"effectiveDate": "2026-07-01"},
        {"asOfDate": "2026-07-31"},
        {"originType": "derived"},
        {"presenceStatus": "not_disclosed"},
        {"formulaId": "formula:gfa"},
        {"formulaVersion": "1.0.0"},
        {"inputFactIds": ["candidate:input"]},
        {"calculationRunId": "calculation-run:1"},
        {"calculationEngineVersion": "engine@1.0.0"},
        {"calculationOutputPath": "/outputs/gfa"},
        {"calculationOutputHash": "e" * 64},
        {"inputSnapshotHash": "f" * 64},
    )

    for mutation in mutations:
        assert build_domain_candidate_content_hash(
            {**candidate, **mutation}
        ) != baseline


def test_reconciliation_reports_unit_conflict_without_comparing_values():
    output = ProductMixParserModule().execute(
        _payload(
            {
                "domain": "product_mix",
                "fieldKey": "program.gross_floor_area_sqm",
                "rawValue": 1000,
                "normalizedValue": 1000,
                "unit": "sqm",
                "sourceLocator": _locator(cell="I3"),
            }
        ),
        CONTEXT,
    )
    second = copy.deepcopy(output["candidates"][0])
    second["candidateId"] = "candidate:unit-conflict"
    second["rawValue"] = 302.5
    second["normalizedValue"] = 302.5
    second["unit"] = "py"
    output["candidates"].append(second)
    output["summary"]["candidateCount"] = 2
    report = _reconcile(output)
    codes = {item["code"] for item in report["diagnostics"]}

    assert "CROSS_SOURCE_UNIT_CONFLICT" in codes
    assert "CROSS_SOURCE_VALUE_CONFLICT" not in codes


def test_collector_classifies_unit_difference_before_value_difference():
    captured = []

    class CapturingConflictStore:
        def create_source_conflict(self, **kwargs):
            captured.append(kwargs)
            return kwargs

    conflict_ids = _persist_value_conflicts(
        records=[
            {
                "candidateId": "candidate:sqm",
                "canonicalField": "program.gross_floor_area_sqm",
                "entityId": None,
                "normalizedValue": 1000,
                "unit": "sqm",
                "metadata": {},
            },
            {
                "candidateId": "candidate:py",
                "canonicalField": "program.gross_floor_area_sqm",
                "entityId": None,
                "normalizedValue": 302.5,
                "unit": "py",
                "metadata": {},
            },
        ],
        record_store=CapturingConflictStore(),
        tenant_id="tenant-1",
        project_id="project-1",
        parsing_run_id="parsing-run:unit-conflict",
        actor_id="parser-worker-1",
    )

    assert len(conflict_ids) == 1
    assert captured[0]["conflict_code"] == (
        "DOMAIN_CANDIDATE_UNIT_CONFLICT"
    )


def test_reconciliation_uses_decimal_totals_and_detects_real_mismatch():
    base_rows = [
        {
            "domain": "cost",
            "fieldKey": "cost.total_project_cost",
            "rawValue": 1000.3,
            "normalizedValue": 1000.3,
            "unit": "krw",
            "basis": _revenue_cost_basis(),
            "sourceLocator": _locator(cell="J1"),
        },
        {
            "domain": "cost",
            "fieldKey": "cost.line_item.amount",
            "entityId": "cost-line:first",
            "rawValue": 400.1,
            "normalizedValue": 400.1,
            "unit": "krw",
            "basis": _revenue_cost_basis(),
            "sourceLocator": _locator(cell="J2"),
        },
        {
            "domain": "cost",
            "fieldKey": "cost.line_item.amount",
            "entityId": "cost-line:second",
            "rawValue": 600.2,
            "normalizedValue": 600.2,
            "unit": "krw",
            "basis": _revenue_cost_basis(),
            "sourceLocator": _locator(cell="J3"),
        },
    ]
    balanced = CostParserModule().execute(
        _payload_rows(base_rows), CONTEXT
    )
    balanced_report = _reconcile(balanced)
    balanced_codes = {
        item["code"] for item in balanced_report["diagnostics"]
    }
    assert "COST_TOTAL_RECONCILIATION_FAILED" not in balanced_codes

    mismatched_rows = [dict(item) for item in base_rows]
    mismatched_rows[0] = {
        **mismatched_rows[0],
        "rawValue": 1005.3,
        "normalizedValue": 1005.3,
    }
    mismatched = CostParserModule().execute(
        _payload_rows(mismatched_rows), CONTEXT
    )
    mismatched_report = _reconcile(mismatched)

    assert "COST_TOTAL_RECONCILIATION_FAILED" in {
        item["code"] for item in mismatched_report["diagnostics"]
    }


def test_reconciliation_detects_same_value_with_conflicting_basis():
    gross_basis = _revenue_cost_basis()
    net_basis = {**gross_basis, "amountScope": "net"}
    output = CostParserModule().execute(
        _payload_rows(
            [
                {
                    "domain": "cost",
                    "fieldKey": "cost.line_item.amount",
                    "entityId": "cost-line:construction",
                    "rawValue": 1000,
                    "normalizedValue": 1000,
                    "unit": "krw",
                    "basis": gross_basis,
                    "sourceLocator": _locator(cell="K1"),
                },
                {
                    "domain": "cost",
                    "fieldKey": "cost.line_item.amount",
                    "entityId": "cost-line:construction",
                    "rawValue": 1000,
                    "normalizedValue": 1000,
                    "unit": "krw",
                    "basis": net_basis,
                    "sourceLocator": _locator(cell="K2"),
                },
            ]
        ),
        CONTEXT,
    )
    report = _reconcile(output)
    codes = {item["code"] for item in report["diagnostics"]}

    assert "CROSS_SOURCE_BASIS_CONFLICT" in codes
    assert "CROSS_SOURCE_VALUE_CONFLICT" not in codes


def test_reconciliation_enforces_expected_entity_ids_and_counts():
    product_output = ProductMixParserModule().execute(
        _payload(
            {
                "domain": "product_mix",
                "fieldKey": "program.product.asset_type",
                "entityId": "product:apartment",
                "rawValue": "apartment",
                "normalizedValue": "apartment",
                "sourceLocator": _locator(cell="L1"),
            }
        ),
        CONTEXT,
    )
    mapping = {
        "metadata": {
            "expectedEntities": {
                "schemaVersion": EXPECTED_ENTITY_POLICY_VERSION,
                "groups": {
                    "product": {
                        "entityIds": [
                            "product:apartment",
                            "product:officetel",
                        ],
                        "count": 2,
                    }
                },
            }
        }
    }
    report = _reconcile(product_output, canonical_mapping=mapping)
    codes = {item["code"] for item in report["diagnostics"]}

    assert "EXPECTED_ENTITY_ID_COVERAGE_MISMATCH" in codes
    assert "EXPECTED_ENTITY_COUNT_MISMATCH" in codes


def test_expected_entity_policy_must_cover_every_repeatable_namespace():
    cost_output = CostParserModule().execute(
        _payload(
            {
                "domain": "cost",
                "fieldKey": "cost.line_item.amount",
                "entityId": "cost-line:one",
                "rawValue": 1000,
                "normalizedValue": 1000,
                "unit": "krw",
                "basis": _revenue_cost_basis(),
                "sourceLocator": _locator(cell="L12"),
            }
        ),
        CONTEXT,
    )
    report = _reconcile(
        cost_output,
        canonical_mapping={
            "metadata": {
                "expectedEntities": {
                    "schemaVersion": EXPECTED_ENTITY_POLICY_VERSION,
                    "groups": {
                        "product": {"entityIds": [], "count": 0}
                    },
                }
            }
        },
    )
    missing = [
        item
        for item in report["diagnostics"]
        if item["code"] == "EXPECTED_ENTITY_GROUP_POLICY_MISSING"
    ]

    assert [item["fieldKey"] for item in missing] == ["costLine"]


def test_repeatable_facts_require_expected_entity_policy_but_scalar_facts_do_not():
    repeatable_output = ProductMixParserModule().execute(
        _payload(
            {
                "domain": "product_mix",
                "fieldKey": "program.product.asset_type",
                "entityId": "product:one",
                "rawValue": "apartment",
                "normalizedValue": "apartment",
                "sourceLocator": _locator(cell="L2"),
            }
        ),
        CONTEXT,
    )
    repeatable_report = _reconcile(repeatable_output)
    repeatable_policy = next(
        item
        for item in repeatable_report["diagnostics"]
        if item["code"] == "EXPECTED_ENTITY_POLICY_NOT_DECLARED"
    )
    assert repeatable_policy["blocking"] is True

    scalar_output = ProductMixParserModule().execute(
        _payload(
            {
                "domain": "product_mix",
                "fieldKey": "program.gross_floor_area_sqm",
                "rawValue": 1000,
                "normalizedValue": 1000,
                "unit": "sqm",
                "sourceLocator": _locator(cell="L3"),
            }
        ),
        CONTEXT,
    )
    scalar_report = _reconcile(scalar_output)
    scalar_policy = next(
        item
        for item in scalar_report["diagnostics"]
        if item["code"] == "EXPECTED_ENTITY_POLICY_NOT_DECLARED"
    )
    assert scalar_policy["blocking"] is False


def test_repeatable_mapping_requires_expected_entity_policy_even_with_no_candidate():
    report = _reconcile(
        canonical_mapping={
            "entries": [
                {
                    "fieldKey": "program.product.name",
                    "entityId": "product:omitted",
                }
            ]
        }
    )
    policy = next(
        item
        for item in report["diagnostics"]
        if item["code"] == "EXPECTED_ENTITY_POLICY_NOT_DECLARED"
    )
    assert policy["blocking"] is True


@pytest.mark.parametrize(
    ("alternative_field", "alternative_value", "alternative_unit"),
    (
        ("schedule.milestone.planned_date", "2026-07-01", None),
        ("schedule.milestone.month_index", 3, "month"),
    ),
)
def test_schedule_completeness_accepts_date_or_month_alternative(
    alternative_field,
    alternative_value,
    alternative_unit,
):
    rows = [
        {
            "domain": "schedule",
            "fieldKey": "schedule.milestone.name",
            "entityId": "milestone:start",
            "rawValue": "Construction start",
            "normalizedValue": "Construction start",
            "sourceLocator": _locator(cell="L4"),
        },
        {
            "domain": "schedule",
            "fieldKey": "schedule.milestone.type",
            "entityId": "milestone:start",
            "rawValue": "construction_start",
            "normalizedValue": "construction_start",
            "sourceLocator": _locator(cell="L5"),
        },
        {
            "domain": "schedule",
            "fieldKey": alternative_field,
            "entityId": "milestone:start",
            "rawValue": alternative_value,
            "normalizedValue": alternative_value,
            "unit": alternative_unit,
            "sourceLocator": _locator(cell="L6"),
        },
    ]
    report = _reconcile(
        ScheduleParserModule().execute(_payload_rows(rows), CONTEXT)
    )

    assert "ENTITY_FIELD_GROUP_INCOMPLETE" not in {
        item["code"] for item in report["diagnostics"]
    }


@pytest.mark.parametrize(
    ("alternative_field", "alternative_value", "alternative_unit"),
    (
        ("sales_collection.step.due_date", "2026-08-01", None),
        ("sales_collection.step.due_month_index", 4, "month"),
    ),
)
def test_sales_collection_completeness_accepts_date_or_month_alternative(
    alternative_field,
    alternative_value,
    alternative_unit,
):
    rows = [
        {
            "domain": "sales_collection",
            "fieldKey": "sales_collection.step.stage",
            "entityId": "step:contract",
            "rawValue": "contract",
            "normalizedValue": "contract",
            "sourceLocator": _locator(cell="L7"),
        },
        {
            "domain": "sales_collection",
            "fieldKey": "sales_collection.step.sequence",
            "entityId": "step:contract",
            "rawValue": 1,
            "normalizedValue": 1,
            "sourceLocator": _locator(cell="L8"),
        },
        {
            "domain": "sales_collection",
            "fieldKey": "sales_collection.step.collection_ratio",
            "entityId": "step:contract",
            "rawValue": 1,
            "normalizedValue": 1,
            "unit": "ratio",
            "sourceLocator": _locator(cell="L9"),
        },
        {
            "domain": "sales_collection",
            "fieldKey": "sales_collection.step.product_entity_id",
            "entityId": "step:contract",
            "rawValue": "product:one",
            "normalizedValue": "product:one",
            "sourceLocator": _locator(cell="L10"),
        },
        {
            "domain": "sales_collection",
            "fieldKey": alternative_field,
            "entityId": "step:contract",
            "rawValue": alternative_value,
            "normalizedValue": alternative_value,
            "unit": alternative_unit,
            "sourceLocator": _locator(cell="L11"),
        },
    ]
    report = _reconcile(
        SalesCollectionParserModule().execute(
            _payload_rows(rows), CONTEXT
        )
    )

    assert "ENTITY_FIELD_GROUP_INCOMPLETE" not in {
        item["code"] for item in report["diagnostics"]
    }


def test_sales_collection_ratios_are_reconciled_per_product_foreign_key():
    product_output = ProductMixParserModule().execute(
        _payload_rows(
            [
                {
                    "domain": "product_mix",
                    "fieldKey": "program.product.asset_type",
                    "entityId": "product:a",
                    "rawValue": "apartment",
                    "normalizedValue": "apartment",
                    "sourceLocator": _locator(cell="M1"),
                },
                {
                    "domain": "product_mix",
                    "fieldKey": "program.product.asset_type",
                    "entityId": "product:b",
                    "rawValue": "officetel",
                    "normalizedValue": "officetel",
                    "sourceLocator": _locator(cell="M2"),
                },
            ]
        ),
        CONTEXT,
    )
    rows = []
    for cell, step_id, product_id, ratio in (
        ("N1", "step:a1", "product:a", 0.4),
        ("N2", "step:a2", "product:a", 0.6),
        ("N3", "step:b1", "product:b", 0.5),
        ("N4", "step:b2", "product:b", 0.4),
    ):
        rows.extend(
            (
                {
                    "domain": "sales_collection",
                    "fieldKey": (
                        "sales_collection.step.product_entity_id"
                    ),
                    "entityId": step_id,
                    "rawValue": product_id,
                    "normalizedValue": product_id,
                    "sourceLocator": _locator(cell=cell),
                },
                {
                    "domain": "sales_collection",
                    "fieldKey": "sales_collection.step.collection_ratio",
                    "entityId": step_id,
                    "rawValue": ratio,
                    "normalizedValue": ratio,
                    "unit": "ratio",
                    "sourceLocator": _locator(cell=f"O{cell[1:]}"),
                },
            )
        )
    sales_output = SalesCollectionParserModule().execute(
        _payload_rows(rows), CONTEXT
    )
    report = _reconcile(product_output, sales_output)
    ratio_diagnostics = [
        item
        for item in report["diagnostics"]
        if item["code"] == "SALES_COLLECTION_RATIO_RECONCILIATION_FAILED"
    ]

    assert [item["entityId"] for item in ratio_diagnostics] == ["product:b"]


def test_pipeline_maps_literal_workbook_cell_persists_candidate_and_seals_package():
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    document = _xlsx_document()
    document_version_id = "document-version:domain-e2e"

    result = run_structural_parsing_use_case(
        document=document,
        actor_id="parser-worker-1",
        profile=feasibility_domain_profile(
            project_id="project-1",
            canonical_workbook_mapping=_mapping(),
        ),
        orchestrator=build_feasibility_domain_orchestrator(),
        record_store=store,
        document_version_id=document_version_id,
        parsing_run_id="parsing-run:domain-e2e",
        source_authority="submitted_document",
    )

    assert result["parsingRun"]["executionStatus"] == "completed"
    assert result["candidateCollection"]["candidateCount"] == 1
    assert result["candidateCollection"]["clean"] is True
    assert result["candidateCollection"]["unresolvedModuleIds"] == []
    assert result["candidateCollection"]["reconciliationBound"] is True
    assert result["candidateSetSeal"]["candidateCount"] == 1
    assert result["candidateSetSeal"]["reconciliationBound"] is True
    assert result["candidateSetSeal"]["reconciliationSourceCandidateIds"] == (
        result["candidateSetSeal"]["candidateIds"]
    )
    candidates = store.list_candidates(
        "tenant-1",
        "project-1",
        run_id="parsing-run:domain-e2e",
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["canonicalField"] == "program.gross_floor_area_sqm"
    assert candidate["normalizedValue"] == 1000
    assert candidate["documentVersionId"] == document_version_id
    assert candidate["sourceHash"] == document.sha256
    assert candidate["authority"] == "submitted_document"
    assert candidate["validationStatus"] == "passed"
    assert candidate["metadata"]["domainModuleId"] == PRODUCT_MIX_MODULE_ID
    assert candidate["metadata"]["mappingAttestationHash"] == (
        canonical_workbook_mapping_hash(_mapping())
    )
    assert candidate["metadata"]["unitEvidenceMode"] == (
        "immutable_profile_mapping_attestation"
    )
    assert len(candidate["metadata"]["normalizationInputHash"]) == 64
    assert len(candidate["metadata"]["normalizationOutputHash"]) == 64

    sanitized_mapping_rows = result["orchestration"]["results"][
        CANONICAL_WORKBOOK_MAPPING_MODULE_ID
    ]["output"]["structuredFactRows"]
    assert "rawValue" not in sanitized_mapping_rows[0]
    assert "normalizedValue" not in sanitized_mapping_rows[0]
    sanitized_candidates = result["orchestration"]["results"][
        PRODUCT_MIX_MODULE_ID
    ]["output"]["candidates"]
    assert "rawValue" not in sanitized_candidates[0]
    assert "normalizedValue" not in sanitized_candidates[0]

    replay = run_structural_parsing_use_case(
        document=document,
        actor_id="parser-worker-1",
        profile=feasibility_domain_profile(
            project_id="project-1",
            canonical_workbook_mapping=_mapping(),
        ),
        orchestrator=build_feasibility_domain_orchestrator(),
        record_store=store,
        document_version_id=document_version_id,
        parsing_run_id="parsing-run:domain-e2e",
        source_authority="submitted_document",
    )
    assert replay["replayed"] is True
    assert replay["candidateSetSeal"]["candidateIds"] == result[
        "candidateSetSeal"
    ]["candidateIds"]


def test_automatic_domain_run_seals_zero_candidate_collection():
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    mapping = _mapping()
    mapping["entries"][0]["domain"] = "unmapped_domain"

    result = run_structural_parsing_use_case(
        document=_xlsx_document(),
        actor_id="parser-worker-1",
        profile=feasibility_domain_profile(
            project_id="project-1",
            canonical_workbook_mapping=mapping,
        ),
        orchestrator=build_feasibility_domain_orchestrator(),
        record_store=store,
        document_version_id="document-version:zero-candidates",
        parsing_run_id="parsing-run:zero-candidates",
        source_authority="submitted_document",
    )

    assert result["parsingRun"]["executionStatus"] == "completed"
    assert result["candidateCollection"]["candidateCount"] == 0
    assert result["candidateCollection"]["reconciliationBound"] is True
    assert result["candidateSetSeal"]["candidateCount"] == 0
    assert result["candidateSetSeal"]["candidateIds"] == []
    assert result["candidateSetSeal"]["unresolvedModuleIds"] == []
    assert len(result["candidateSetSeal"]["collectionManifestHash"]) == 64

    with pytest.raises(ParserV2ApprovalError, match="closed|sealed"):
        store.create_candidate(
            tenant_id="tenant-1",
            project_id="project-1",
            run_id="parsing-run:zero-candidates",
            document_version_id="document-version:zero-candidates",
            candidate_id="candidate:late",
            actor_id="parser-worker-1",
            canonical_field="program.gross_floor_area_sqm",
            value=1,
            unit="sqm",
        )


def test_open_conflict_is_sealed_but_remains_a_review_gate():
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    mapping = {
        "schemaVersion": CANONICAL_WORKBOOK_MAPPING_VERSION,
        "documentType": "feasibility_workbook",
        "entries": [
            {
                "domain": "product_mix",
                "fieldKey": "program.gross_floor_area_sqm",
                "sheetName": "Feasibility",
                "cellReference": cell,
                "unit": "sqm",
            }
            for cell in ("A1", "A2")
        ],
    }
    result = run_structural_parsing_use_case(
        document=_xlsx_document_cells({"A1": 1000, "A2": 1100}),
        actor_id="parser-worker-1",
        profile=feasibility_domain_profile(
            project_id="project-1",
            canonical_workbook_mapping=mapping,
        ),
        orchestrator=build_feasibility_domain_orchestrator(),
        record_store=store,
        document_version_id="document-version:conflict",
        parsing_run_id="parsing-run:conflict",
        source_authority="submitted_document",
    )

    assert result["parsingRun"]["executionStatus"] == (
        "completed_with_warnings"
    )
    assert result["candidateSetSeal"]["candidateCount"] == 2
    assert result["candidateSetSeal"]["conflictCount"] == 1
    assert "CROSS_SOURCE_VALUE_CONFLICT" in (
        result["candidateSetSeal"]["blockingDiagnosticCodes"]
    )
    assert result["candidateCollection"]["reviewEligible"] is True
    assert result["candidateCollection"]["approvalEligible"] is False

    candidate_id = result["candidateSetSeal"]["candidateIds"][0]
    review = store.append_review_decision(
        tenant_id="tenant-1",
        project_id="project-1",
        candidate_id=candidate_id,
        actor_id="human-reviewer",
        action="approve",
        reason="Conflict will be explicitly resolved before selection.",
        conflict_ids=result["candidateSetSeal"]["conflictIds"],
    )
    assert review["approvalStatus"] == "approved"
    with pytest.raises(ParserV2ApprovalError, match="open source conflicts"):
        store.create_canonical_selection(
            tenant_id="tenant-1",
            project_id="project-1",
            selection_id="selection:blocked-conflict",
            actor_id="human-reviewer",
            candidate_id=candidate_id,
            review_decision_id=review["reviewDecisionId"],
        )


def test_collector_rejects_locator_not_attested_by_structural_artifact():
    run_id = CONTEXT["parsing_run_id"]
    store, _ = _ready_running_store(run_id=run_id)
    domain_output = ProductMixParserModule().execute(
        _payload(
            {
                "domain": "product_mix",
                "fieldKey": "program.gross_floor_area_sqm",
                "rawValue": 1000,
                "normalizedValue": 1000,
                "unit": "sqm",
                "sourceLocator": _locator(cell="P1"),
            }
        ),
        CONTEXT,
    )
    candidate = domain_output["candidates"][0]
    candidate["evidence"][0]["evidenceId"] = "evidence:forged"
    candidate["metadata"]["candidateContentHash"] = (
        build_domain_candidate_content_hash(candidate)
    )
    candidate["candidateId"] = build_domain_candidate_id(candidate)
    structural_output = {
        "artifact": {
            "chunks": [{"sourceLocator": _locator(cell="P1")}],
        }
    }
    orchestration = _orchestration_result(
        {
            "structural_extraction": _module_execution(
                "structural_extraction", structural_output
            ),
            PRODUCT_MIX_MODULE_ID: _module_execution(
                PRODUCT_MIX_MODULE_ID, domain_output
            ),
        }
    )

    with pytest.raises(
        ParserModuleCandidateCollectionError,
        match="not emitted by structural extraction",
    ):
        collect_parser_module_candidates(
            orchestration_result=orchestration,
            record_store=store,
            tenant_id="tenant-1",
            project_id="project-1",
            parsing_run_id=run_id,
            document_version_id=DOCUMENT_VERSION_ID,
            source_hash=SOURCE_HASH,
            actor_id="parser-worker-1",
            ontology_version=CONTEXT["ontology_version"],
            expected_module_ids=(PRODUCT_MIX_MODULE_ID,),
        )


def test_partial_module_manifest_is_sealed_as_blocking_completeness():
    run_id = "parsing-run:partial-module"
    store, _ = _ready_running_store(run_id=run_id)
    product_output = ProductMixParserModule().execute(
        _payload(
            {
                "domain": "product_mix",
                "fieldKey": "program.gross_floor_area_sqm",
                "rawValue": 1000,
                "normalizedValue": 1000,
                "unit": "sqm",
                "sourceLocator": _locator(cell="Q1"),
            }
        ),
        {**CONTEXT, "parsing_run_id": run_id},
    )
    collection = collect_parser_module_candidates(
        orchestration_result=_orchestration_result(
            {
                "structural_extraction": _module_execution(
                    "structural_extraction",
                    {
                        "artifact": {
                            "chunks": [
                                {"sourceLocator": _locator(cell="Q1")}
                            ]
                        }
                    },
                ),
                PRODUCT_MIX_MODULE_ID: _module_execution(
                    PRODUCT_MIX_MODULE_ID, product_output
                )
            },
            status="partial",
        ),
        record_store=store,
        tenant_id="tenant-1",
        project_id="project-1",
        parsing_run_id=run_id,
        document_version_id=DOCUMENT_VERSION_ID,
        source_hash=SOURCE_HASH,
        actor_id="parser-worker-1",
        ontology_version=CONTEXT["ontology_version"],
        expected_module_ids=(
            PRODUCT_MIX_MODULE_ID,
            RECONCILIATION_MODULE_ID,
        ),
    )

    assert collection.completed_module_ids == (PRODUCT_MIX_MODULE_ID,)
    assert collection.unresolved_module_ids == (RECONCILIATION_MODULE_ID,)
    assert "DOMAIN_MODULE_COMPLETENESS_UNRESOLVED" in (
        collection.blocking_diagnostic_codes
    )
    assert "RECONCILIATION_CANDIDATE_SET_BINDING_MISSING" in (
        collection.blocking_diagnostic_codes
    )
    store.append_parsing_run_event(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=run_id,
        actor_id="parser-worker-1",
        execution_status="completed_with_warnings",
        error_codes=["PARTIAL_MODULE_TEST"],
    )
    seal = seal_collected_candidate_set(
        collection=collection,
        record_store=store,
        tenant_id="tenant-1",
        project_id="project-1",
        parsing_run_id=run_id,
        actor_id="parser-worker-1",
    )
    assert seal["candidateCount"] == 1
    assert seal["unresolvedModuleIds"] == [RECONCILIATION_MODULE_ID]
    assert seal["reconciliationBound"] is False
    with pytest.raises(
        ParserV2ApprovalError,
        match="unresolved parser modules",
    ):
        store.append_review_decision(
            tenant_id="tenant-1",
            project_id="project-1",
            candidate_id=seal["candidateIds"][0],
            actor_id="human-reviewer",
            action="approve",
            reason="Incomplete module set must never enter human approval.",
        )


def test_collector_requires_reconciliation_to_bind_exact_candidate_set():
    run_id = "parsing-run:bad-reconciliation-set"
    store, _ = _ready_running_store(run_id=run_id)
    reconciliation_output = {
        "schemaVersion": (
            "realytics-feasibility-domain-module-output-v1"
        ),
        "outputKind": "reconciliation_report",
        "moduleId": RECONCILIATION_MODULE_ID,
        "moduleVersion": "1.0.0",
        "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
        "domain": "same_run_cross_field_reconciliation",
        "candidateOnly": True,
        "authoritative": False,
        "candidates": [],
        "diagnostics": [],
        "summary": {
            "sourceModuleCount": 0,
            "candidateCount": 1,
            "blockingDiagnosticCount": 0,
        },
        "sourceModuleIds": [],
        "sourceCandidateIds": ["candidate:invented"],
        "pendingCapabilities": [],
        "providedCapabilities": [],
    }

    with pytest.raises(
        ParserModuleCandidateCollectionError,
        match="candidate set does not match",
    ):
        collect_parser_module_candidates(
            orchestration_result=_orchestration_result(
                {
                    RECONCILIATION_MODULE_ID: _module_execution(
                        RECONCILIATION_MODULE_ID,
                        reconciliation_output,
                    )
                }
            ),
            record_store=store,
            tenant_id="tenant-1",
            project_id="project-1",
            parsing_run_id=run_id,
            document_version_id=DOCUMENT_VERSION_ID,
            source_hash=SOURCE_HASH,
            actor_id="parser-worker-1",
            ontology_version=CONTEXT["ontology_version"],
            expected_module_ids=(RECONCILIATION_MODULE_ID,),
        )


@pytest.mark.parametrize("terminal_status", ("failed", "cancelled"))
def test_terminal_unsuccessful_run_rejects_late_candidate_injection(
    terminal_status,
):
    run_id = f"parsing-run:{terminal_status}"
    store, _ = _ready_running_store(run_id=run_id)
    store.append_parsing_run_event(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=run_id,
        actor_id="parser-worker-1",
        execution_status=terminal_status,
        error_codes=(
            ["INJECTED_FAILURE"] if terminal_status == "failed" else []
        ),
    )

    with pytest.raises(
        ParserV2ApprovalError,
        match="failed or cancelled",
    ):
        store.create_candidate(
            tenant_id="tenant-1",
            project_id="project-1",
            run_id=run_id,
            document_version_id=DOCUMENT_VERSION_ID,
            candidate_id=f"candidate:late:{terminal_status}",
            actor_id="parser-worker-1",
            canonical_field="program.gross_floor_area_sqm",
            value=1000,
            unit="sqm",
        )


def test_pipeline_formula_mapping_fails_closed_without_candidate_or_seal():
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)

    result = run_structural_parsing_use_case(
        document=_xlsx_document(formula=True),
        actor_id="parser-worker-1",
        profile=feasibility_domain_profile(
            project_id="project-1",
            canonical_workbook_mapping=_mapping(),
        ),
        orchestrator=build_feasibility_domain_orchestrator(),
        record_store=store,
        document_version_id="document-version:formula-e2e",
        parsing_run_id="parsing-run:formula-e2e",
        source_authority="submitted_document",
    )

    assert result["parsingRun"]["executionStatus"] == "failed"
    assert "candidateSetSeal" not in result
    assert (
        store.list_candidates(
            "tenant-1",
            "project-1",
            run_id="parsing-run:formula-e2e",
        )
        == []
    )
    diagnostics = result["orchestration"]["results"][
        CANONICAL_WORKBOOK_MAPPING_MODULE_ID
    ]["output"]["diagnostics"]
    assert "WORKBOOK_FORMULA_RECALCULATION_REQUIRED" in {
        item["code"] for item in diagnostics
    }


def test_completed_replay_repairs_transient_candidate_set_seal_failure():
    memory = MemoryRecords()

    class FailOnceSealStore(ParserV2RecordStore):
        def __init__(self):
            super().__init__(upsert_fn=memory.upsert, list_fn=memory.list)
            self.fail_once = True

        def seal_candidate_set(self, **kwargs):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("injected seal persistence interruption")
            return super().seal_candidate_set(**kwargs)

    store = FailOnceSealStore()
    document = _xlsx_document()
    kwargs = {
        "document": document,
        "actor_id": "parser-worker-1",
        "profile": feasibility_domain_profile(
            project_id="project-1",
            canonical_workbook_mapping=_mapping(),
        ),
        "orchestrator": build_feasibility_domain_orchestrator(),
        "record_store": store,
        "document_version_id": "document-version:seal-repair",
        "parsing_run_id": "parsing-run:seal-repair",
        "source_authority": "submitted_document",
    }

    with pytest.raises(
        RuntimeError, match="injected seal persistence interruption"
    ):
        run_structural_parsing_use_case(**kwargs)

    completed = store.get_parsing_run(
        "tenant-1", "project-1", "parsing-run:seal-repair"
    )
    assert completed["executionStatus"] == "completed"
    assert (
        store.list_candidate_set_seals("tenant-1", "project-1") == []
    )
    with pytest.raises(
        ParserV2ApprovalError,
        match="automatic domain candidate collection is closed",
    ):
        store.create_candidate(
            tenant_id="tenant-1",
            project_id="project-1",
            run_id="parsing-run:seal-repair",
            document_version_id="document-version:seal-repair",
            candidate_id="candidate:seal-crash-injection",
            actor_id="parser-worker-1",
            canonical_field="program.gross_floor_area_sqm",
            value=9999,
            unit="sqm",
        )

    replay = run_structural_parsing_use_case(**kwargs)

    assert replay["replayed"] is True
    assert replay["candidateSetSeal"]["candidateCount"] == 1
    assert len(
        store.list_candidate_set_seals("tenant-1", "project-1")
    ) == 1


def test_unpinned_runtime_mapping_injection_is_rejected_before_records():
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)

    with pytest.raises(ValueError, match="hash-pinned"):
        run_structural_parsing_use_case(
            document=_xlsx_document(),
            actor_id="parser-worker-1",
            profile=feasibility_domain_profile(project_id="project-1"),
            orchestrator=build_feasibility_domain_orchestrator(),
            record_store=store,
            document_version_id="document-version:unpinned",
            parsing_run_id="parsing-run:unpinned",
            source_authority="submitted_document",
            canonical_workbook_mapping=_mapping(),
        )

    assert memory.records == {}


def test_profile_mapping_hash_tampering_is_rejected_before_records():
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    profile = feasibility_domain_profile(
        project_id="project-1",
        canonical_workbook_mapping=_mapping(),
    )
    profile_wire = profile.to_dict()
    profile_wire["metadata"]["canonicalWorkbookMappingHash"] = "0" * 64
    tampered_profile = type(profile).from_value(profile_wire)

    with pytest.raises(ValueError, match="does not match"):
        run_structural_parsing_use_case(
            document=_xlsx_document(),
            actor_id="parser-worker-1",
            profile=tampered_profile,
            orchestrator=build_feasibility_domain_orchestrator(),
            record_store=store,
            document_version_id="document-version:tampered-profile-hash",
            parsing_run_id="parsing-run:tampered-profile-hash",
            source_authority="submitted_document",
        )

    assert memory.records == {}


def test_domain_pack_imports_do_not_create_default_module_cycle():
    import parsing_platform.default_modules as default_modules
    import parsing_platform.feasibility_domain_modules as domain_modules

    assert default_modules.build_default_parser_module_registry
    assert domain_modules.build_feasibility_domain_module_registry
    assert {
        PRODUCT_MIX_MODULE_ID,
        COST_MODULE_ID,
        SCHEDULE_MODULE_ID,
        SALES_COLLECTION_MODULE_ID,
        PF_TERMS_MODULE_ID,
    } == set(DOMAIN_MODULE_IDS)
