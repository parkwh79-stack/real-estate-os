from __future__ import annotations

"""Production Parser V2 runtime composed from independently selectable packs.

Registration and selection are deliberately separate concerns: this runtime
knows about Intake Core, Feasibility Domain Pack, and PDF enrichment modules,
while a project profile still pins the exact subset and versions that execute.
The default project profile therefore remains the three-module Intake Core.
"""

from collections.abc import Mapping

from parsing_platform.adapters import DocumentAdapterRegistry
from parsing_platform.default_modules import build_default_parser_module_registry
from parsing_platform.feasibility_domain_modules import (
    CanonicalWorkbookFactMappingModule,
    CostParserModule,
    PfTermsParserModule,
    ProductMixParserModule,
    SalesCollectionParserModule,
    SameRunCrossFieldReconciliationModule,
    ScheduleParserModule,
    build_feasibility_domain_schema_registry,
)
from parsing_platform.orchestrator import ParsingOrchestrator
from parsing_platform.pdf_enrichment_modules import (
    PdfNativeTableModule,
    PdfOcrAssessmentDispatchPlanModule,
    SemanticSectionModule,
    register_pdf_enrichment_schema_contracts,
)
from parsing_platform.pdf_structure_enrichment import NativePdfTableExtractor
from parsing_platform.registry import ParserModuleRegistry
from parsing_platform.schema_contracts import SchemaContractRegistry


def build_parser_v2_runtime_module_registry(
    *,
    adapter_registry: DocumentAdapterRegistry | None = None,
    table_extractor: NativePdfTableExtractor | None = None,
    ocr_environ: Mapping[str, str] | None = None,
) -> ParserModuleRegistry:
    """Register every deployable in-process Parser V2 module exactly once."""

    registry = build_default_parser_module_registry(
        adapter_registry=adapter_registry
    )
    for module in (
        CanonicalWorkbookFactMappingModule(),
        ProductMixParserModule(),
        CostParserModule(),
        ScheduleParserModule(),
        SalesCollectionParserModule(),
        PfTermsParserModule(),
        SameRunCrossFieldReconciliationModule(),
        PdfNativeTableModule(table_extractor),
        SemanticSectionModule(),
        PdfOcrAssessmentDispatchPlanModule(environ=ocr_environ),
    ):
        registry.register(module)
    return registry


def build_parser_v2_runtime_schema_registry() -> SchemaContractRegistry:
    """Compose immutable default, feasibility, and PDF schema contracts."""

    registry = build_feasibility_domain_schema_registry()
    return register_pdf_enrichment_schema_contracts(registry)


def build_parser_v2_runtime_orchestrator(
    *,
    adapter_registry: DocumentAdapterRegistry | None = None,
    table_extractor: NativePdfTableExtractor | None = None,
    ocr_environ: Mapping[str, str] | None = None,
) -> ParsingOrchestrator:
    """Build the single router/runtime orchestrator used in production."""

    return ParsingOrchestrator(
        build_parser_v2_runtime_module_registry(
            adapter_registry=adapter_registry,
            table_extractor=table_extractor,
            ocr_environ=ocr_environ,
        ),
        schema_registry=build_parser_v2_runtime_schema_registry(),
    )


__all__ = [
    "build_parser_v2_runtime_module_registry",
    "build_parser_v2_runtime_orchestrator",
    "build_parser_v2_runtime_schema_registry",
]
