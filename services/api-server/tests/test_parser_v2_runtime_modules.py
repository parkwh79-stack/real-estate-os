from parsing_platform.default_modules import (
    build_default_intake_orchestrator,
    intake_core_profile,
)
from parsing_platform.feasibility_domain_modules import (
    CANONICAL_WORKBOOK_MAPPING_VERSION,
    feasibility_domain_profile,
)
from parsing_platform.pdf_enrichment_modules import (
    PDF_NATIVE_TABLE_MANIFEST,
    PDF_OCR_PLANNING_MANIFEST,
    SEMANTIC_SECTION_MANIFEST,
    pdf_enrichment_profile,
)
from parsing_platform.runtime_modules import (
    build_parser_v2_runtime_orchestrator,
)


def _plan_refs(orchestrator, profile):
    return [
        module.manifest.ref
        for module in orchestrator.build_plan(profile).modules
    ]


def test_runtime_registry_has_a_schema_contract_for_every_registered_module():
    orchestrator = build_parser_v2_runtime_orchestrator(
        ocr_environ={},
    )
    schema_ids = {
        item["schemaId"]
        for item in orchestrator.schema_registry.snapshot()
    }

    assert len(orchestrator.registry.snapshot()) == 14
    for manifest in orchestrator.registry.list_active():
        assert manifest.input_schema in schema_ids
        assert manifest.output_schema in schema_ids


def test_runtime_registration_does_not_change_default_intake_execution_plan():
    runtime = build_parser_v2_runtime_orchestrator(ocr_environ={})
    intake_only = build_default_intake_orchestrator()
    profile = intake_core_profile(project_id="project-1")

    assert _plan_refs(runtime, profile) == _plan_refs(
        intake_only,
        profile,
    ) == [
        "document_inspection@1.0.0",
        "structural_extraction@1.0.0",
        "document_classification@1.0.0",
    ]
    assert runtime.build_plan(profile).to_dict() == (
        intake_only.build_plan(profile).to_dict()
    )


def test_single_runtime_resolves_both_optional_pack_profiles():
    runtime = build_parser_v2_runtime_orchestrator(ocr_environ={})

    feasibility_refs = _plan_refs(
        runtime,
        feasibility_domain_profile(
            project_id="project-1",
            canonical_workbook_mapping={
                "schemaVersion": CANONICAL_WORKBOOK_MAPPING_VERSION,
                "documentType": "feasibility_workbook",
                "entries": [],
            },
        ),
    )
    assert "feasibility_product_mix@1.0.0" in feasibility_refs
    assert "feasibility_reconciliation@1.0.0" in feasibility_refs

    pdf_refs = _plan_refs(
        runtime,
        pdf_enrichment_profile(project_id="project-1"),
    )
    assert PDF_NATIVE_TABLE_MANIFEST.ref in pdf_refs
    assert SEMANTIC_SECTION_MANIFEST.ref in pdf_refs
    assert PDF_OCR_PLANNING_MANIFEST.ref in pdf_refs
