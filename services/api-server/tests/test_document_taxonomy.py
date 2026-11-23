from __future__ import annotations

import pytest

from agents.document_classifier_agent import DOC_TYPES, classify_document
from parsing_platform.default_modules import (
    DocumentClassificationModule,
    DocumentInspectionModule,
    StructuralExtractionModule,
    build_default_parser_module_registry,
)
from parsing_platform.document_taxonomy import (
    ALL_CANONICAL_DOCUMENT_TYPES,
    CLASSIFIER_DOCUMENT_TYPES,
    DOCUMENT_TAXONOMY_VERSION,
    DOCUMENT_TYPE_CONTRACTS,
    DOCUMENT_TYPE_SCOPE_ALL,
    DOCUMENT_TYPE_SCOPE_LISTED,
    canonical_document_type,
    document_taxonomy_catalog,
)
from parsing_platform.domain_standards import DOMAIN_STANDARDS, domain_standard_catalog
from parsing_platform.orchestrator import (
    InvalidProfileError,
    ProjectParsingProfile,
)
from parsing_platform.registry import InvalidManifestError, ParserModuleManifest


def test_classifier_and_domain_standards_share_canonical_taxonomy_version() -> None:
    classified = classify_document("business_plan.pdf")
    catalog = domain_standard_catalog()
    taxonomy = document_taxonomy_catalog()

    assert classified["taxonomyVersion"] == DOCUMENT_TAXONOMY_VERSION
    assert catalog["documentTaxonomyVersion"] == DOCUMENT_TAXONOMY_VERSION
    assert taxonomy["schemaVersion"] == DOCUMENT_TAXONOMY_VERSION
    assert set(DOCUMENT_TYPE_CONTRACTS) == set(ALL_CANONICAL_DOCUMENT_TYPES)
    assert tuple(DOC_TYPES) == CLASSIFIER_DOCUMENT_TYPES
    assert classified["documentType"] in DOCUMENT_TYPE_CONTRACTS
    assert {
        document_type
        for standard in DOMAIN_STANDARDS
        for document_type in standard.document_types
    }.issubset(ALL_CANONICAL_DOCUMENT_TYPES)


def test_legacy_architectural_plan_alias_normalizes_without_duplicate_namespace() -> None:
    assert canonical_document_type("architectural_plan") == (
        "basic_architectural_plan"
    )
    assert "architectural_plan" not in ALL_CANONICAL_DOCUMENT_TYPES
    permit_standard = next(
        item for item in DOMAIN_STANDARDS if item.domain.value == "permit_planning"
    )
    assert "basic_architectural_plan" in permit_standard.document_types
    assert "architectural_plan" not in permit_standard.document_types


def test_default_manifests_use_explicit_all_scope_or_supported_canonical_types() -> None:
    inspection = DocumentInspectionModule.manifest
    structure = StructuralExtractionModule.manifest
    classification = DocumentClassificationModule.manifest

    assert inspection.document_taxonomy_version == DOCUMENT_TAXONOMY_VERSION
    assert inspection.document_type_scope == DOCUMENT_TYPE_SCOPE_ALL
    assert inspection.supported_document_types == ()
    assert structure.document_taxonomy_version == DOCUMENT_TAXONOMY_VERSION
    assert structure.document_type_scope == DOCUMENT_TYPE_SCOPE_ALL
    assert structure.supported_document_types == ()
    assert classification.document_taxonomy_version == DOCUMENT_TAXONOMY_VERSION
    assert classification.document_type_scope == DOCUMENT_TYPE_SCOPE_LISTED
    assert classification.supported_document_types == CLASSIFIER_DOCUMENT_TYPES
    assert all(
        "*" not in manifest.supported_document_types
        for manifest in (inspection, structure, classification)
    )


def test_legacy_wildcard_and_unversioned_manifests_fail_closed() -> None:
    with pytest.raises(InvalidManifestError, match="Legacy '\\*'.*forbidden"):
        ParserModuleManifest.from_value(
            {
                "moduleId": "legacy_universal",
                "version": "1.0.0",
                "capabilities": ["document.inspect"],
                "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
                "supportedDocumentTypes": ["*"],
            }
        )

    with pytest.raises(
        InvalidManifestError,
        match="document_taxonomy_version is required",
    ):
        ParserModuleManifest.from_value(
            {
                "moduleId": "unversioned_legacy",
                "version": "1.0.0",
                "capabilities": ["document.inspect"],
                "documentTypeScope": "all",
            }
        )


def test_explicit_all_scope_is_still_bounded_by_the_canonical_taxonomy() -> None:
    registry = build_default_parser_module_registry()

    canonical_candidates = {
        item.module_id
        for item in registry.find_candidates(document_type="business_plan")
    }
    assert "document_inspection" in canonical_candidates
    assert "structural_extraction" in canonical_candidates
    assert registry.find_candidates(document_type="pdf") == []
    assert registry.find_candidates(document_type="unknown_future_type") == []


def test_taxonomy_aware_manifest_normalizes_alias_and_rejects_namespace_drift() -> None:
    manifest = ParserModuleManifest.from_value(
        {
            "moduleId": "architecture_parser",
            "version": "1.0.0",
            "capabilities": ["candidate.architecture"],
            "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
            "documentTypeScope": "listed",
            "supportedDocumentTypes": ["architectural_plan"],
        }
    )

    assert manifest.supported_document_types == ("basic_architectural_plan",)

    with pytest.raises(InvalidManifestError, match="Unknown .* document type"):
        ParserModuleManifest(
            module_id="format_as_semantic_type",
            version="1.0.0",
            capabilities=("document.structure",),
            document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
            supported_document_types=("pdf",),
        )

    with pytest.raises(
        InvalidManifestError,
        match="Listed document-type scope requires",
    ):
        ParserModuleManifest(
            module_id="implicit_universal",
            version="1.0.0",
            capabilities=("document.inspect",),
            document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        )


def test_profile_requires_versioned_nonempty_canonical_document_types() -> None:
    base = {
        "profileId": "taxonomy-profile",
        "version": "1.0.0",
        "selectedModules": ["document_classification@1.0.0"],
    }
    with pytest.raises(
        InvalidProfileError,
        match="document_taxonomy_version is required",
    ):
        ProjectParsingProfile.from_value(
            {
                **base,
                "documentTypes": ["business_plan"],
            }
        )
    with pytest.raises(
        InvalidProfileError,
        match="document_types must list",
    ):
        ProjectParsingProfile.from_value(
            {
                **base,
                "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
                "documentTypes": [],
            }
        )
    with pytest.raises(
        InvalidProfileError,
        match="Unknown .* document type",
    ):
        ProjectParsingProfile.from_value(
            {
                **base,
                "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
                "documentTypes": ["pdf"],
            }
        )

    profile = ProjectParsingProfile.from_value(
        {
            **base,
            "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
            "documentTypes": ["architectural_plan"],
        }
    )
    assert profile.document_taxonomy_version == DOCUMENT_TAXONOMY_VERSION
    assert profile.document_types == ("basic_architectural_plan",)


def test_manifest_rejects_unrecognized_taxonomy_version() -> None:
    with pytest.raises(InvalidManifestError, match="Unsupported document taxonomy"):
        ParserModuleManifest(
            module_id="future_taxonomy",
            version="1.0.0",
            capabilities=("document.classify",),
            document_taxonomy_version="realytics-document-taxonomy-v999",
            supported_document_types=("business_plan",),
        )
