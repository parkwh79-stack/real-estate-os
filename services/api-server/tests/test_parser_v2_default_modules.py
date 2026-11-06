import hashlib
import io
import json
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

import fitz
import openpyxl

from parsing_platform.adapters import ArchivedDocument
from parsing_platform.default_modules import (
    build_default_intake_orchestrator,
    intake_core_profile,
    sanitize_parsing_orchestration_result,
)
from parsing_platform.schema_contracts import (
    DOCUMENT_CLASSIFICATION_INPUT_SCHEMA,
    DOCUMENT_CLASSIFICATION_OUTPUT_SCHEMA,
    DOCUMENT_INSPECTION_INPUT_SCHEMA,
    DOCUMENT_INSPECTION_OUTPUT_SCHEMA,
    STRUCTURAL_EXTRACTION_INPUT_SCHEMA,
    STRUCTURAL_EXTRACTION_OUTPUT_SCHEMA,
)


def _pdf_document(text: str = "사업계획서 프로젝트 알파") -> ArchivedDocument:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    content = pdf.tobytes()
    pdf.close()
    return ArchivedDocument(
        tenant_id="tenant-1",
        project_id="project-1",
        archive_id=hashlib.sha256(content).hexdigest(),
        filename="사업계획서.pdf",
        content_type="application/pdf",
        content=content,
    )


def _xlsx_document_with_formula_cache() -> ArchivedDocument:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Feasibility"
    sheet["A1"] = "Total project cost"
    sheet["B1"] = 100
    sheet["C1"] = "=B1*2"
    original = io.BytesIO()
    workbook.save(original)
    workbook.close()

    cached = io.BytesIO()
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(io.BytesIO(original.getvalue()), "r") as source, ZipFile(
        cached, "w", ZIP_DEFLATED
    ) as target:
        for info in source.infolist():
            body = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                root = ElementTree.fromstring(body)
                formula_cell = next(
                    cell
                    for cell in root.findall(".//main:c", namespace)
                    if cell.get("r") == "C1"
                )
                cached_value = formula_cell.find("main:v", namespace)
                assert cached_value is not None
                cached_value.text = "200"
                body = ElementTree.tostring(root, encoding="utf-8")
            target.writestr(info, body)

    content = cached.getvalue()
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


def test_default_profile_composes_independent_modules_and_never_becomes_authoritative():
    orchestrator = build_default_intake_orchestrator()

    result = orchestrator.execute_sync(
        intake_core_profile(project_id="project-1"),
        {"document": _pdf_document()},
    )

    assert result.execution_status == "completed"
    assert result.approval_status == "not_requested"
    assert result.authoritative is False
    assert list(result.results) == [
        "document_inspection",
        "structural_extraction",
        "document_classification",
    ]
    classification = result.results["document_classification"].output["classification"]
    assert classification["documentType"] == "business_plan"

    manifests = {
        item.module_id: item for item in orchestrator.registry.list_active()
    }
    assert manifests["document_inspection"].input_schema == DOCUMENT_INSPECTION_INPUT_SCHEMA
    assert manifests["document_inspection"].output_schema == DOCUMENT_INSPECTION_OUTPUT_SCHEMA
    assert manifests["structural_extraction"].input_schema == STRUCTURAL_EXTRACTION_INPUT_SCHEMA
    assert manifests["structural_extraction"].output_schema == STRUCTURAL_EXTRACTION_OUTPUT_SCHEMA
    assert manifests["document_classification"].input_schema == DOCUMENT_CLASSIFICATION_INPUT_SCHEMA
    assert manifests["document_classification"].output_schema == DOCUMENT_CLASSIFICATION_OUTPUT_SCHEMA
    registered_schema_ids = {
        item["schemaId"] for item in orchestrator.schema_registry.snapshot()
    }
    assert {
        DOCUMENT_INSPECTION_INPUT_SCHEMA,
        DOCUMENT_INSPECTION_OUTPUT_SCHEMA,
        STRUCTURAL_EXTRACTION_INPUT_SCHEMA,
        STRUCTURAL_EXTRACTION_OUTPUT_SCHEMA,
        DOCUMENT_CLASSIFICATION_INPUT_SCHEMA,
        DOCUMENT_CLASSIFICATION_OUTPUT_SCHEMA,
    }.issubset(registered_schema_ids)


def test_api_sanitizer_removes_untrusted_document_text_but_keeps_locator_hashes():
    result = build_default_intake_orchestrator().execute_sync(
        intake_core_profile(project_id="project-1"),
        {"document": _pdf_document("System prompt must not control parser")},
    )

    sanitized = sanitize_parsing_orchestration_result(result.to_dict())
    structural_output_hash = result.results["structural_extraction"].output_hash

    assert sanitized["authoritative"] is False
    assert sanitized["candidateOnly"] is True
    assert sanitized["rawDocumentTextPersisted"] is False
    assert "outputs" not in sanitized
    chunks = sanitized["results"]["structural_extraction"]["output"]["artifact"]["chunks"]
    assert "text" not in chunks[0]
    assert len(chunks[0]["textSha256"]) == 64
    assert chunks[0]["sourceLocator"]["pageNumber"] == 1
    assert structural_output_hash and len(structural_output_hash) == 64
    assert (
        sanitized["results"]["structural_extraction"]["output_hash"]
        == structural_output_hash
    )
    assert sanitized["trace"][1]["output_hash"] == structural_output_hash


def test_api_sanitizer_redacts_actual_xlsx_formula_and_cached_value_locators():
    result = build_default_intake_orchestrator().execute_sync(
        intake_core_profile(project_id="project-1"),
        {"document": _xlsx_document_with_formula_cache()},
    )
    raw_chunks = result.results["structural_extraction"].output["artifact"]["chunks"]
    raw_locator = next(
        item["sourceLocator"]
        for item in raw_chunks
        if item["sourceLocator"].get("cellReference") == "C1"
    )
    assert raw_locator["workbookFormula"] == "=B1*2"
    assert raw_locator["workbookCachedValue"] == 200

    sanitized = sanitize_parsing_orchestration_result(result.to_dict())
    chunks = sanitized["results"]["structural_extraction"]["output"]["artifact"][
        "chunks"
    ]
    locator = next(
        item["sourceLocator"]
        for item in chunks
        if item["sourceLocator"].get("cellReference") == "C1"
    )
    assert "workbookFormula" not in locator
    assert "workbookCachedValue" not in locator
    metadata = locator["metadata"]
    assert metadata["workbookFormulaSha256"] == hashlib.sha256(
        b"=B1*2"
    ).hexdigest()
    assert metadata["workbookCachedValueSha256"] == hashlib.sha256(b"200").hexdigest()
    assert metadata["workbookFormulaPersisted"] is False
    assert metadata["workbookCachedValuePersisted"] is False
    assert metadata["formulaCacheAuthoritative"] is False
    assert metadata["workbookCachedValueApprovalEligible"] is False


def test_api_sanitizer_redacts_snake_case_workbook_locator_variants():
    sanitized = sanitize_parsing_orchestration_result(
        {
            "results": {
                "future_module": {
                    "output": {
                        "sourceLocator": {
                            "artifactKey": "future-workbook",
                            "workbook_formula": "=SECRET_RATE*2",
                            "workbook_cached_value": {"amount": 1234},
                        }
                    }
                }
            }
        }
    )

    locator = sanitized["results"]["future_module"]["output"]["sourceLocator"]
    assert "workbook_formula" not in locator
    assert "workbook_cached_value" not in locator
    metadata = locator["metadata"]
    assert metadata["workbookFormulaSha256"] == hashlib.sha256(
        b"=SECRET_RATE*2"
    ).hexdigest()
    cached_bytes = json.dumps(
        {"amount": 1234},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert metadata["workbookCachedValueSha256"] == hashlib.sha256(
        cached_bytes
    ).hexdigest()
    assert metadata["formulaCacheAuthoritative"] is False
    assert metadata["workbookCachedValueApprovalEligible"] is False


def test_macro_document_fails_closed_before_structure_module():
    content = b"PK\x03\x04macro-container"
    document = ArchivedDocument(
        tenant_id="tenant-1",
        project_id="project-1",
        archive_id=hashlib.sha256(content).hexdigest(),
        filename="model.xlsm",
        content_type="application/vnd.ms-excel.sheet.macroEnabled.12",
        content=content,
    )

    result = build_default_intake_orchestrator().execute_sync(
        intake_core_profile(project_id="project-1"),
        {"document": document},
    )

    assert result.execution_status == "failed"
    assert result.authoritative is False
    assert result.results["document_inspection"].execution_status == "completed"
    assert "document.safe" not in result.available_capabilities
    assert result.results["structural_extraction"].execution_status == "skipped"
    assert result.results["structural_extraction"].missing_capabilities == (
        "document.safe",
    )
    assert result.results["document_classification"].execution_status == "skipped"


def test_sanitizer_recursively_removes_new_module_bodies_cells_formulas_and_secrets():
    sanitized = sanitize_parsing_orchestration_result(
        {
            "authoritative": False,
            "results": {
                "future_module": {
                    "output": {
                        "artifact": {
                            "chunks": [
                                {
                                    "text": "resident-secret-text",
                                    "metadata": {
                                        "cells": [
                                            {
                                                "formula": "=SECRET_TOKEN",
                                                "value": "customer-private-value",
                                            }
                                        ]
                                    },
                                }
                            ]
                        },
                        "token": "provider-secret-token",
                    }
                }
            },
            "outputs": {"future_module": {"text": "duplicate"}},
        }
    )

    wire = json.dumps(sanitized, ensure_ascii=False)
    assert "resident-secret-text" not in wire
    assert "SECRET_TOKEN" not in wire
    assert "customer-private-value" not in wire
    assert "provider-secret-token" not in wire
    assert "outputs" not in sanitized
