from __future__ import annotations

from typing import Any

import pytest

from parsing_platform.document_taxonomy import DOCUMENT_TAXONOMY_VERSION
from parsing_platform.orchestrator import (
    DependencyCycleError,
    InvalidProfileError,
    MissingCapabilityError,
    ParsingOrchestrator,
)
from parsing_platform.registry import (
    ParserModuleRegistry,
    ParsingRegistryError,
)
from parsing_platform.schema_contracts import (
    DuplicateSchemaContractError,
    GENERIC_MODULE_OUTPUT_SCHEMA,
    SchemaContractRegistry,
    UnknownSchemaContractError,
    build_default_schema_contract_registry,
)


def _taxonomy_profile_fields(
    *document_types: str,
) -> dict[str, Any]:
    return {
        "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
        "documentTypes": list(document_types or ("business_plan",)),
    }


class FakeModel:
    """Small Pydantic-shaped object proving the platform has no Pydantic dependency."""

    def __init__(self, value: dict[str, Any]):
        self.value = value

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return dict(self.value)


class RecordingModule:
    def __init__(
        self,
        manifest: dict[str, Any] | FakeModel,
        *,
        fail: bool = False,
        model_result: bool = False,
    ) -> None:
        self.manifest = manifest
        self.fail = fail
        self.model_result = model_result
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def execute(self, payload, context):
        self.calls.append((dict(payload), dict(context)))
        if self.fail:
            raise RuntimeError(f"{context['module_id']} failed")
        result = {
            "module": context["module_id"],
            "upstream": sorted(payload["module_results"]),
            "document_id": payload["input"].get("document_id"),
        }
        return FakeModel(result) if self.model_result else result


class AsyncRecordingModule(RecordingModule):
    async def execute(self, payload, context):
        self.calls.append((dict(payload), dict(context)))
        if self.fail:
            raise RuntimeError(f"{context['module_id']} failed")
        return {
            "module": context["module_id"],
            "upstream": sorted(payload["module_results"]),
            "document_id": payload["input"].get("document_id"),
        }


def _manifest(
    module_id: str,
    *,
    requires: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    version: str = "1.0.0",
    supported_document_types: tuple[str, ...] = (
        "business_plan",
        "feasibility_workbook",
    ),
) -> dict[str, Any]:
    return {
        "moduleId": module_id,
        "version": version,
        "capabilities": [f"execute.{module_id}"],
        "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
        "documentTypeScope": "listed",
        "supportedDocumentTypes": list(supported_document_types),
        "supportedAssetTypes": ["residential", "logistics"],
        "supportedDealTypes": ["development_pf", "acquisition_finance"],
        "requires": list(requires),
        "provides": list(provides),
    }


def _module_id(module: RecordingModule) -> str:
    manifest = module.manifest.model_dump() if hasattr(module.manifest, "model_dump") else module.manifest
    return manifest["moduleId"]


@pytest.fixture
def industry_modules():
    """Default real-estate parsing modules requested by the V2 composition contract."""

    return [
        RecordingModule(
            FakeModel(_manifest("document_classifier", provides=("document.classified",)))
        ),
        RecordingModule(
            _manifest(
                "native_extractor",
                requires=("document.classified",),
                provides=("content.extracted",),
            )
        ),
        AsyncRecordingModule(
            _manifest(
                "ocr_fallback",
                requires=("document.classified",),
                provides=("content.extracted",),
            )
        ),
        RecordingModule(
            _manifest(
                "ontology_mapper",
                requires=("content.extracted",),
                provides=("facts.mapped",),
            ),
            model_result=True,
        ),
        RecordingModule(
            _manifest(
                "normalizer",
                requires=("facts.mapped",),
                provides=("facts.normalized",),
            )
        ),
        RecordingModule(
            _manifest(
                "reconciler",
                requires=("facts.normalized",),
                provides=("facts.reconciled",),
            )
        ),
        RecordingModule(
            _manifest(
                "approval_projector",
                requires=("facts.reconciled",),
                provides=("approval.candidates_projected",),
            )
        ),
    ]


@pytest.fixture
def registry(industry_modules):
    result = ParserModuleRegistry()
    for module in industry_modules:
        result.register(module)
    return result


@pytest.fixture
def profile():
    module_ids = [
        "document_classifier",
        "native_extractor",
        "ocr_fallback",
        "ontology_mapper",
        "normalizer",
        "reconciler",
        "approval_projector",
    ]
    return FakeModel(
        {
            "profileId": "development-pf-standard",
            "version": "2.0.0",
            "projectId": "project-101",
            "selectedModules": module_ids,
            "excludedModules": [],
            "failurePolicy": "preserve_partial",
            **_taxonomy_profile_fields(
                "business_plan",
                "feasibility_workbook",
            ),
            "assetTypes": ["residential"],
            "dealTypes": ["development_pf"],
            "orderedStages": [
                {"stageId": "classification", "moduleIds": ["document_classifier"]},
                {
                    "stageId": "extraction",
                    "moduleIds": ["native_extractor", "ocr_fallback"],
                },
                {
                    "stageId": "semantics",
                    "moduleIds": ["ontology_mapper", "normalizer"],
                },
                {"stageId": "reconciliation", "moduleIds": ["reconciler"]},
                {"stageId": "approval_projection", "moduleIds": ["approval_projector"]},
            ],
        }
    )


def test_registry_accepts_model_manifest_selects_latest_and_filters_candidates():
    registry = ParserModuleRegistry()
    v1 = RecordingModule(FakeModel(_manifest("native_extractor", version="1.2.0")))
    v2 = RecordingModule(FakeModel(_manifest("native_extractor", version="1.10.0")))
    registry.register(v1)
    registry.register(v2)

    assert registry.get("native_extractor") is v2
    assert registry.resolve("native_extractor@1.2.0") is v1
    assert [
        item.ref
        for item in registry.find_candidates(document_type="business_plan")
    ] == [
        "native_extractor@1.2.0",
        "native_extractor@1.10.0",
    ]
    assert registry.find_candidates(document_type="hwp") == []

    with pytest.raises(ParsingRegistryError, match="already registered"):
        registry.register(v1)


def test_project_version_pin_overrides_an_unpinned_stage_reference():
    registry = ParserModuleRegistry()
    registry.register(RecordingModule(_manifest("native_extractor", version="1.2.0")))
    registry.register(RecordingModule(_manifest("native_extractor", version="2.0.0")))
    profile = {
        "profile_id": "pinned-project",
        "version": "1",
        **_taxonomy_profile_fields(),
        "selected_modules": ["native_extractor@1.2.0"],
        "ordered_stages": [
            {"stage_id": "extraction", "module_refs": ["native_extractor"]}
        ],
    }

    plan = ParsingOrchestrator(registry).build_plan(profile)

    assert plan.modules[0].manifest.version == "1.2.0"


def test_profile_builds_ordered_dependency_plan_and_can_exclude_a_fallback(
    registry, profile
):
    raw = profile.model_dump()
    raw["excludedModules"] = ["ocr_fallback"]
    raw["selectedModules"] = [
        module for module in raw["selectedModules"] if module != "ocr_fallback"
    ]

    plan = ParsingOrchestrator(registry).build_plan(raw)

    assert [module.module_id for module in plan.modules] == [
        "document_classifier",
        "native_extractor",
        "ontology_mapper",
        "normalizer",
        "reconciler",
        "approval_projector",
    ]
    assert plan.dependencies["native_extractor"] == ("document_classifier",)
    assert plan.dependencies["ontology_mapper"] == ("native_extractor",)
    assert [stage.stage_id for stage in plan.stages] == [
        "classification",
        "extraction",
        "semantics",
        "reconciliation",
        "approval_projection",
    ]


def test_missing_capability_is_rejected_before_execution(registry, profile):
    raw = profile.model_dump()
    raw["selectedModules"] = ["native_extractor"]

    with pytest.raises(MissingCapabilityError) as exc_info:
        ParsingOrchestrator(registry).build_plan(raw)

    assert exc_info.value.missing == {
        "native_extractor": ("document.classified",),
    }


def test_cycle_is_detected_deterministically():
    registry = ParserModuleRegistry()
    registry.register(
        RecordingModule(_manifest("cycle_a", requires=("value.b",), provides=("value.a",)))
    )
    registry.register(
        RecordingModule(_manifest("cycle_b", requires=("value.a",), provides=("value.b",)))
    )
    profile = {
        "profile_id": "cycle-test",
        "version": "1",
        **_taxonomy_profile_fields(),
        "selected_modules": ["cycle_a", "cycle_b"],
        "ordered_stages": [
            {"stage_id": "cycle", "module_refs": ["cycle_a", "cycle_b"]}
        ],
    }

    with pytest.raises(DependencyCycleError) as exc_info:
        ParsingOrchestrator(registry).build_plan(profile)

    assert exc_info.value.module_ids == ("cycle_a", "cycle_b")


def test_non_mapping_module_result_is_a_traced_boundary_failure():
    class BadResultModule:
        manifest = _manifest("bad_result", provides=("bad.output",))

        def execute(self, payload, context):
            return ["not", "a", "mapping"]

    registry = ParserModuleRegistry()
    registry.register(BadResultModule())
    profile = {
        "profile_id": "bad-result",
        "version": "1",
        **_taxonomy_profile_fields(),
        "selected_modules": ["bad_result"],
        "failure_policy": "preserve_partial",
    }

    result = ParsingOrchestrator(registry).execute_sync(profile, {"document_id": "bad"})

    assert result.execution_status == "partial"
    assert result.results["bad_result"].error_code == "InvalidModuleResultError"


def test_pipeline_executes_sync_and_async_modules_and_aggregates_results(registry, profile):
    orchestrator = ParsingOrchestrator(registry)
    payload = FakeModel({"document_id": "doc-1", "content_sha256": "abc"})

    result = orchestrator.execute_sync(profile, payload, context={"trace_id": "trace-a"})
    replay = orchestrator.execute_sync(
        profile,
        {"content_sha256": "abc", "document_id": "doc-1"},
        context={"trace_id": "trace-b"},
    )

    assert result.execution_status == "completed"
    assert result.idempotency_key == replay.idempotency_key
    assert result.failed_module_ids == ()
    assert list(result.outputs) == [
        "document_classifier",
        "native_extractor",
        "ocr_fallback",
        "ontology_mapper",
        "normalizer",
        "reconciler",
        "approval_projector",
    ]
    assert result.outputs["ontology_mapper"]["upstream"] == [
        "native_extractor",
        "ocr_fallback",
    ]
    assert result.outputs["normalizer"]["upstream"] == [
        "ontology_mapper",
    ]
    assert [item.execution_status for item in result.trace] == ["completed"] * 7
    assert len({item.idempotency_key for item in result.trace}) == 7
    assert all(
        item.output_hash and len(item.output_hash) == 64
        for item in result.results.values()
    )
    assert {
        item.module_id: item.output_hash for item in result.trace
    } == {
        module_id: item.output_hash for module_id, item in result.results.items()
    }
    assert {
        module_id: item.output_hash for module_id, item in result.results.items()
    } == {
        module_id: item.output_hash for module_id, item in replay.results.items()
    }

    changed = orchestrator.execute_sync(
        profile, {"document_id": "doc-2", "content_sha256": "abc"}
    )
    assert changed.idempotency_key != result.idempotency_key
    assert changed.results["document_classifier"].output_hash != result.results[
        "document_classifier"
    ].output_hash


def test_execution_completion_never_grants_business_authority(registry, profile):
    result = ParsingOrchestrator(registry).execute_sync(
        profile,
        {"document_id": "doc-authority", "content_sha256": "sha"},
    )

    serialized = result.to_dict()
    assert result.execution_status == "completed"
    assert result.approval_status == "not_requested"
    assert result.authoritative is False
    assert serialized["execution_status"] == "completed"
    assert serialized["approval_status"] == "not_requested"
    assert serialized["authoritative"] is False
    assert "status" not in serialized


def test_preserve_partial_uses_ocr_fallback_then_runs_downstream(industry_modules, profile):
    native = next(
        module
        for module in industry_modules
        if _module_id(module) == "native_extractor"
    )
    native.fail = True
    registry = ParserModuleRegistry()
    for module in industry_modules:
        registry.register(module)

    result = ParsingOrchestrator(registry).execute_sync(
        profile, {"document_id": "scanned-doc", "content_sha256": "scan"}
    )

    assert result.execution_status == "partial"
    assert result.authoritative is False
    assert result.failed_module_ids == ("native_extractor",)
    assert result.results["native_extractor"].execution_status == "failed"
    assert result.results["ocr_fallback"].execution_status == "completed"
    assert result.results["approval_projector"].execution_status == "completed"
    assert "approval.candidates_projected" in result.available_capabilities


def test_fail_closed_stops_remaining_modules(industry_modules, profile):
    native = next(
        module
        for module in industry_modules
        if _module_id(module) == "native_extractor"
    )
    native.fail = True
    registry = ParserModuleRegistry()
    for module in industry_modules:
        registry.register(module)
    raw = profile.model_dump()
    raw["failurePolicy"] = "fail_closed"

    result = ParsingOrchestrator(registry).execute_sync(
        raw, {"document_id": "doc-closed", "content_sha256": "closed"}
    )

    assert result.execution_status == "failed"
    assert result.failed_module_ids == ("native_extractor",)
    assert result.results["ocr_fallback"].execution_status == "skipped"
    assert result.results["approval_projector"].execution_status == "skipped"
    assert result.outputs.keys() == {"document_classifier"}
    assert result.authoritative is False


def test_stage_can_run_independently_with_explicit_capability_and_upstream_results(
    registry, profile
):
    result = ParsingOrchestrator(registry).execute_stage_sync(
        profile,
        "semantics",
        {"document_id": "doc-stage", "content_sha256": "stage"},
        available_capabilities=["content.extracted"],
        upstream_results={"native_extractor": {"facts": [1, 2]}},
    )

    assert result.execution_status == "completed"
    assert [item.module_id for item in result.trace] == ["ontology_mapper", "normalizer"]
    assert result.results["native_extractor"].execution_status == "completed"
    assert result.results["normalizer"].execution_status == "completed"
    assert "facts.normalized" in result.available_capabilities


def test_selected_module_must_belong_to_an_ordered_stage(registry, profile):
    raw = profile.model_dump()
    raw["selectedModules"].append("unlisted_module")

    with pytest.raises(InvalidProfileError, match="not assigned"):
        ParsingOrchestrator(registry).build_plan(raw)


def test_explicit_module_must_match_profile_document_type():
    registry = ParserModuleRegistry()
    registry.register(
        RecordingModule(
            _manifest(
                "business_plan_only",
                provides=("content.business_plan",),
                supported_document_types=("business_plan",),
            )
        )
    )

    with pytest.raises(InvalidProfileError, match="not compatible"):
        ParsingOrchestrator(registry).build_plan(
            {
                "profileId": "feasibility-project",
                "version": "1",
                **_taxonomy_profile_fields("feasibility_workbook"),
                "selectedModules": ["business_plan_only"],
            }
        )


def test_execution_scope_mismatch_fails_before_module_call(registry, profile):
    with pytest.raises(InvalidProfileError, match="project scopes do not match"):
        ParsingOrchestrator(registry).execute_sync(
            profile,
            {"document_id": "doc-1"},
            context={"project_id": "another-project"},
        )


def test_module_exception_details_are_not_exposed():
    class SecretFailure:
        manifest = _manifest("secret_failure", provides=("secret.output",))

        def execute(self, payload, context):
            raise RuntimeError("token=must-not-leak")

    registry = ParserModuleRegistry()
    registry.register(SecretFailure())
    result = ParsingOrchestrator(registry).execute_sync(
        {
            "profileId": "secret-test",
            "version": "1",
            **_taxonomy_profile_fields(),
            "selectedModules": ["secret_failure"],
            "failurePolicy": "preserve_partial",
        },
        {"document_id": "doc-1"},
    )

    assert result.results["secret_failure"].error_code == "RuntimeError"
    assert "must-not-leak" not in result.results["secret_failure"].error_message


def test_capability_binding_version_must_match_resolved_provider():
    registry = ParserModuleRegistry()
    registry.register(
        RecordingModule(_manifest("provider", version="1", provides=("value.ready",)))
    )
    registry.register(
        RecordingModule(
            _manifest(
                "consumer",
                requires=("value.ready",),
                provides=("value.used",),
            )
        )
    )

    with pytest.raises(InvalidProfileError, match="unavailable provider"):
        ParsingOrchestrator(registry).build_plan(
            {
                "profileId": "binding-version",
                "version": "1",
                **_taxonomy_profile_fields(),
                "selectedModules": ["provider@1", "consumer"],
                "capabilityBindings": {"value.ready": ["provider@2"]},
            }
        )


def test_schema_registry_rejects_conflicting_duplicate_schema_id():
    schemas = SchemaContractRegistry()
    schemas.register(
        "custom-module-boundary-v1",
        lambda _payload: None,
        definition={"type": "object", "required": ["first"]},
    )

    with pytest.raises(DuplicateSchemaContractError, match="already registered"):
        schemas.register(
            "custom-module-boundary-v1",
            lambda _payload: None,
            definition={"type": "object", "required": ["conflicting"]},
        )


def test_explicit_unknown_schema_fails_during_plan_and_execute():
    manifest = _manifest("unknown_schema_module", provides=("unknown.output",))
    manifest["inputSchema"] = "custom-unregistered-input-v1"
    module = RecordingModule(manifest)
    registry = ParserModuleRegistry()
    registry.register(module)
    orchestrator = ParsingOrchestrator(registry)
    profile = {
        "profileId": "unknown-schema",
        "version": "1",
        **_taxonomy_profile_fields(),
        "selectedModules": ["unknown_schema_module"],
    }

    with pytest.raises(UnknownSchemaContractError, match="unregistered"):
        orchestrator.build_plan(profile)
    with pytest.raises(UnknownSchemaContractError, match="unregistered"):
        orchestrator.execute_sync(profile, {"document_id": "never-executed"})
    assert module.calls == []


def test_input_schema_blocks_module_call_without_leaking_rejected_payload():
    schemas = build_default_schema_contract_registry()

    def reject_input(payload):
        if "required_marker" not in payload["input"]:
            raise ValueError(
                "secret-token=must-not-leak; body=" + str(payload["input"])
            )

    schemas.register(
        "custom-required-input-v1",
        reject_input,
        definition={"type": "module-envelope", "requiredInput": ["required_marker"]},
    )
    manifest = _manifest("input_guarded", provides=("guarded.output",))
    manifest["inputSchema"] = "custom-required-input-v1"
    manifest["outputSchema"] = GENERIC_MODULE_OUTPUT_SCHEMA
    module = RecordingModule(manifest)
    registry = ParserModuleRegistry()
    registry.register(module)

    result = ParsingOrchestrator(registry, schema_registry=schemas).execute_sync(
        {
            "profileId": "input-schema-guard",
            "version": "1",
            **_taxonomy_profile_fields(),
            "selectedModules": ["input_guarded"],
            "failurePolicy": "preserve_partial",
        },
        {"document_id": "doc-1", "secret": "customer-private-body"},
    )

    failed = result.results["input_guarded"]
    assert result.execution_status == "partial"
    assert module.calls == []
    assert failed.error_code == "MODULE_INPUT_SCHEMA_VALIDATION_FAILED"
    assert "customer-private-body" not in failed.error_message
    assert "must-not-leak" not in failed.error_message


def test_output_schema_rejects_bad_shape_without_leaking_module_output():
    schemas = build_default_schema_contract_registry()

    def reject_output(payload):
        if payload.get("contractApproved") is not True:
            raise ValueError("provider-secret=must-not-leak; output=" + str(payload))

    schemas.register(
        "custom-approved-output-v1",
        reject_output,
        definition={"type": "object", "required": ["contractApproved"]},
    )
    manifest = _manifest("output_guarded", provides=("guarded.output",))
    manifest["outputSchema"] = "custom-approved-output-v1"
    module = RecordingModule(manifest)
    registry = ParserModuleRegistry()
    registry.register(module)

    result = ParsingOrchestrator(registry, schema_registry=schemas).execute_sync(
        {
            "profileId": "output-schema-guard",
            "version": "1",
            **_taxonomy_profile_fields(),
            "selectedModules": ["output_guarded"],
            "failurePolicy": "preserve_partial",
        },
        {"document_id": "private-output-value"},
    )

    failed = result.results["output_guarded"]
    assert result.execution_status == "partial"
    assert len(module.calls) == 1
    assert failed.output is None
    assert failed.error_code == "MODULE_OUTPUT_SCHEMA_VALIDATION_FAILED"
    assert "private-output-value" not in failed.error_message
    assert "must-not-leak" not in failed.error_message
