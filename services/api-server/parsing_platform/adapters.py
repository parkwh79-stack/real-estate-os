from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Protocol
from xml.etree import ElementTree

import fitz
import openpyxl


ADAPTER_SCHEMA_VERSION = "realytics-document-adapter-v1"
MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 20_000
MAX_ZIP_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_ZIP_RATIO = 250
MAX_ZIP_ENTRY_BYTES = 100 * 1024 * 1024
MAX_RELATIONSHIP_ENTRY_BYTES = 2 * 1024 * 1024
MAX_DOCX_DOCUMENT_XML_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 5_000
MAX_EXTRACTION_CHUNKS = 100_000
MAX_CHUNK_TEXT_CHARS = 1_000_000


@dataclass(frozen=True)
class ArchivedDocument:
    """Tenant/project-scoped source bytes resolved from the immutable archive."""

    project_id: str
    archive_id: str
    filename: str
    content_type: str | None
    content: bytes
    tenant_id: str | None = None
    object_key: str | None = None
    object_version: str | None = None

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project_id is required")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.archive_id or ""):
            raise ValueError("archive_id must be a SHA-256 hex digest")

    @property
    def extension(self) -> str:
        return Path(self.filename or "").suffix.lower()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class InspectionFinding:
    code: str
    severity: str
    message: str
    blocking: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InspectionReport:
    archive_id: str
    status: str
    media_family: str
    file_size_bytes: int
    sha256_verified: bool
    findings: tuple[InspectionFinding, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return any(item.blocking for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "realytics-document-inspection-v1",
            "archiveId": self.archive_id,
            "status": self.status,
            "mediaFamily": self.media_family,
            "fileSizeBytes": self.file_size_bytes,
            "sha256Verified": self.sha256_verified,
            "blocked": self.blocked,
            "findings": [asdict(item) for item in self.findings],
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ExtractionChunk:
    chunk_id: str
    kind: str
    text: str
    source_locator: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunkId": self.chunk_id,
            "kind": self.kind,
            "text": self.text,
            "sourceLocator": self.source_locator,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ExtractionArtifact:
    adapter_id: str
    adapter_version: str
    document_id: str
    chunks: tuple[ExtractionChunk, ...]
    complete: bool = True
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        chunks = []
        for chunk in self.chunks:
            item = chunk.to_dict()
            if not include_text:
                item["text"] = None
                item["textSha256"] = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
            chunks.append(item)
        return {
            "schemaVersion": ADAPTER_SCHEMA_VERSION,
            "adapterId": self.adapter_id,
            "adapterVersion": self.adapter_version,
            "documentId": self.document_id,
            "complete": self.complete,
            "chunkCount": len(chunks),
            "warnings": list(self.warnings),
            "metadata": self.metadata,
            "chunks": chunks,
        }


class DocumentAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def supports(self, document: ArchivedDocument) -> bool: ...

    def extract(self, document: ArchivedDocument) -> ExtractionArtifact: ...


def bind_artifact_to_document_version(
    artifact: ExtractionArtifact,
    *,
    document: ArchivedDocument,
    document_version_id: str,
    document_id: str | None = None,
) -> ExtractionArtifact:
    """Normalize adapter-local anchors into Parser V2 evidence locators.

    Native adapters intentionally know only immutable archive bytes.  This
    boundary binds every chunk to the persisted document version before a
    domain module may create candidates from it.
    """

    version_id = str(document_version_id or "").strip()
    if not version_id:
        raise ValueError("document_version_id is required to bind extraction evidence")
    bound_chunks: list[ExtractionChunk] = []
    for chunk in artifact.chunks:
        locator = dict(chunk.source_locator)
        metadata = dict(locator.get("metadata") or {})
        bbox = metadata.pop("bbox", None)
        coordinate_space = metadata.pop("bboxCoordinateSystem", None)
        if bbox is not None:
            locator["bbox"] = bbox
            locator["coordinateSpace"] = coordinate_space or "pdf_points_top_left"
        cell_reference = locator.get("cellReference")
        if isinstance(cell_reference, str) and ":" in cell_reference:
            locator["cellRange"] = cell_reference
            locator.pop("cellReference", None)
        chunk_metadata = dict(chunk.metadata or {})
        cells = chunk_metadata.get("cells")
        if isinstance(cells, list) and len(cells) == 1 and isinstance(cells[0], dict):
            cell = cells[0]
            if cell.get("formula") is not None:
                locator["workbookFormula"] = str(cell["formula"])
                locator["workbookCachedValue"] = cell.get("value")
                metadata.update(
                    {
                        "workbookCalculationState": cell.get(
                            "calculationState", "cached_value_unverified"
                        ),
                        "workbookCachedValueApprovalEligible": False,
                        "workbookFormulaSha256": hashlib.sha256(
                            str(cell["formula"]).encode("utf-8")
                        ).hexdigest(),
                    }
                )
        json_path = locator.get("jsonPath")
        if chunk.kind == "docx_paragraph" and isinstance(json_path, str):
            match = re.search(r"/paragraph/(\d+)$", json_path)
            if match:
                locator["paragraphIndex"] = int(match.group(1))
        elif chunk.kind == "docx_table_row" and isinstance(json_path, str):
            match = re.search(r"/table/(\d+)/row/(\d+)$", json_path)
            if match:
                locator["tableId"] = f"table-{match.group(1)}"
                locator["rowIndex"] = int(match.group(2))
        locator.update(
            {
                "documentVersionId": version_id,
                "fileSha256": document.sha256,
                "rawTextSha256": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
                "parserMethod": f"{artifact.adapter_id}@{artifact.adapter_version}",
                "evidenceId": _evidence_id(version_id, chunk.chunk_id),
            }
        )
        locator["documentId"] = str(document_id or locator.get("documentId") or document.archive_id)
        if metadata:
            locator["metadata"] = metadata
        else:
            locator.pop("metadata", None)
        bound_chunks.append(replace(chunk, source_locator=locator))
    return replace(
        artifact,
        document_id=str(document_id or artifact.document_id),
        chunks=tuple(bound_chunks),
        metadata={
            **dict(artifact.metadata),
            "documentVersionId": version_id,
            "fileSha256": document.sha256,
            "locatorsBound": True,
        },
    )


def inspect_archived_document(
    document: ArchivedDocument,
    *,
    max_source_bytes: int = MAX_SOURCE_BYTES,
) -> InspectionReport:
    findings: list[InspectionFinding] = []
    metadata: dict[str, Any] = {}
    media_family = _media_family(document)

    if not document.content:
        findings.append(InspectionFinding("EMPTY_FILE", "critical", "The archived source document is empty.", True))
    if len(document.content) > max_source_bytes:
        findings.append(
            InspectionFinding(
                "FILE_SIZE_LIMIT_EXCEEDED",
                "critical",
                "The source document exceeds the configured parser size limit.",
                True,
                {"limitBytes": max_source_bytes},
            )
        )
    if document.sha256.lower() != document.archive_id.lower():
        findings.append(
            InspectionFinding(
                "ARCHIVE_HASH_MISMATCH",
                "critical",
                "The archived bytes do not match the immutable archive identifier.",
                True,
            )
        )

    magic_finding = _magic_mismatch(document)
    if magic_finding:
        findings.append(magic_finding)

    if media_family == "pdf" and document.content:
        try:
            pdf = fitz.open(stream=document.content, filetype="pdf")
            metadata.update({"pageCount": pdf.page_count, "encrypted": bool(pdf.needs_pass)})
            if pdf.needs_pass:
                findings.append(
                    InspectionFinding(
                        "ENCRYPTED_DOCUMENT",
                        "critical",
                        "Encrypted PDF documents require an approved decryption workflow before parsing.",
                        True,
                    )
                )
            pdf.close()
        except Exception as exc:
            findings.append(
                InspectionFinding(
                    "CORRUPT_OR_UNSUPPORTED_PDF",
                    "critical",
                    "The PDF container could not be opened safely.",
                    True,
                    {"exceptionType": type(exc).__name__},
                )
            )

    if media_family in {"xlsx", "docx"} and document.content:
        zip_findings, zip_metadata = _inspect_zip_container(document.content)
        findings.extend(zip_findings)
        metadata.update(zip_metadata)

    if document.extension in {".xlsm", ".xltm", ".docm", ".dotm"}:
        findings.append(
            InspectionFinding(
                "MACRO_ENABLED_DOCUMENT",
                "critical",
                "Macro-enabled Office documents remain quarantined until malware scanning succeeds.",
                True,
            )
        )

    if document.extension in {".hwp", ".hwpx", ".xls", ".doc"}:
        findings.append(
            InspectionFinding(
                "SANDBOX_ADAPTER_REQUIRED",
                "high",
                "This legacy document format requires a dedicated isolated conversion adapter.",
                True,
                {"extension": document.extension},
            )
        )

    blocked = any(item.blocking for item in findings)
    return InspectionReport(
        archive_id=document.archive_id,
        status="blocked" if blocked else "passed_with_findings" if findings else "passed",
        media_family=media_family,
        file_size_bytes=len(document.content),
        sha256_verified=document.sha256.lower() == document.archive_id.lower(),
        findings=tuple(findings),
        metadata=metadata,
    )


class PdfNativeAdapter:
    adapter_id = "pdf_native_structure"
    adapter_version = "1.0.0"

    def supports(self, document: ArchivedDocument) -> bool:
        return _media_family(document) == "pdf"

    def extract(self, document: ArchivedDocument) -> ExtractionArtifact:
        chunks: list[ExtractionChunk] = []
        warnings: list[str] = []
        pdf = fitz.open(stream=document.content, filetype="pdf")
        page_count = pdf.page_count
        try:
            if pdf.needs_pass:
                raise ValueError("Encrypted PDF cannot be parsed")
            if page_count > MAX_PDF_PAGES:
                raise ValueError("PDF page count exceeds the structural adapter limit")
            for page_index, page in enumerate(pdf, start=1):
                blocks = page.get_text("blocks", sort=True)
                page_text_found = False
                for block_index, block in enumerate(blocks, start=1):
                    text = str(block[4] or "").strip()
                    if not text:
                        continue
                    if len(text) > MAX_CHUNK_TEXT_CHARS:
                        raise ValueError("PDF text block exceeds the structural adapter limit")
                    if len(chunks) >= MAX_EXTRACTION_CHUNKS:
                        warnings.append("ADAPTER_CHUNK_LIMIT_REACHED")
                        break
                    page_text_found = True
                    bbox = [round(float(value), 3) for value in block[:4]]
                    chunks.append(
                        ExtractionChunk(
                            chunk_id=f"page-{page_index}-block-{block_index}",
                            kind="pdf_text_block",
                            text=text,
                            source_locator={
                                "documentId": document.archive_id,
                                "documentName": document.filename,
                                "pageNumber": page_index,
                                "sourceType": "document_page_bbox",
                                "metadata": {
                                    "bbox": bbox,
                                    "bboxCoordinateSystem": "pdf_points_top_left",
                                },
                            },
                        )
                    )
                if not page_text_found:
                    warnings.append(f"OCR_REQUIRED_PAGE:{page_index}")
                if len(chunks) >= MAX_EXTRACTION_CHUNKS:
                    break
        finally:
            pdf.close()
        return ExtractionArtifact(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            document_id=document.archive_id,
            chunks=tuple(chunks),
            complete=not warnings,
            warnings=tuple(warnings),
            metadata={"pageCount": page_count, "ocrRequired": bool(warnings)},
        )


class XlsxStructureAdapter:
    adapter_id = "xlsx_structure"
    adapter_version = "1.0.0"

    def __init__(self, *, max_rows_per_sheet: int = 20_000, max_cells: int = 250_000):
        self.max_rows_per_sheet = max_rows_per_sheet
        self.max_cells = max_cells

    def supports(self, document: ArchivedDocument) -> bool:
        return document.extension in {".xlsx", ".xlsm"}

    def extract(self, document: ArchivedDocument) -> ExtractionArtifact:
        formulas = openpyxl.load_workbook(
            io.BytesIO(document.content), data_only=False, read_only=True, keep_links=False
        )
        values = openpyxl.load_workbook(
            io.BytesIO(document.content), data_only=True, read_only=True, keep_links=False
        )
        chunks: list[ExtractionChunk] = []
        warnings: list[str] = []
        visited_cells = 0
        formula_cell_count = 0
        cached_formula_value_count = 0
        external_formula_reference_count = 0
        calculation = getattr(formulas, "calculation", None) or getattr(
            formulas, "calculation_properties", None
        )
        calculation_properties = {
            "calcMode": getattr(calculation, "calcMode", None),
            "calcId": getattr(calculation, "calcId", None),
            "fullCalcOnLoad": getattr(calculation, "fullCalcOnLoad", None),
            "forceFullCalc": getattr(calculation, "forceFullCalc", None),
        }
        try:
            for sheet_name in formulas.sheetnames:
                formula_sheet = formulas[sheet_name]
                value_sheet = values[sheet_name]
                for row_index, row in enumerate(formula_sheet.iter_rows(), start=1):
                    if row_index > self.max_rows_per_sheet or visited_cells >= self.max_cells:
                        warnings.append(f"ADAPTER_LIMIT_REACHED:{sheet_name}")
                        break
                    for cell in row:
                        if visited_cells >= self.max_cells:
                            warnings.append(f"ADAPTER_LIMIT_REACHED:{sheet_name}")
                            break
                        visited_cells += 1
                        formula = cell.value
                        cached = value_sheet[cell.coordinate].value
                        if formula in (None, "") and cached in (None, ""):
                            continue
                        is_formula = isinstance(formula, str) and formula.startswith("=")
                        has_external_reference = bool(
                            is_formula
                            and re.search(r"\[[^\]]+\]", str(formula))
                        )
                        if is_formula:
                            formula_cell_count += 1
                            if cached is not None:
                                cached_formula_value_count += 1
                            if has_external_reference:
                                external_formula_reference_count += 1
                        calculation_state = (
                            "external_dependency_blocked"
                            if has_external_reference
                            else "cached_value_unverified"
                            if is_formula and cached is not None
                            else "cached_value_missing"
                            if is_formula
                            else "literal"
                        )
                        cell_data = {
                            "cellReference": cell.coordinate,
                            "formula": formula if is_formula else None,
                            "value": (
                                cached
                                if is_formula
                                else formula
                            ),
                            "dataType": cell.data_type,
                            "calculationState": calculation_state,
                            "cachedValuePresent": bool(is_formula and cached is not None),
                            "approvalEligible": not is_formula,
                        }
                        chunks.append(
                            ExtractionChunk(
                                chunk_id=(
                                    f"sheet-{_safe_id(sheet_name)}-cell-{cell.coordinate}"
                                ),
                                kind="worksheet_cell",
                                text=json.dumps(cell_data, ensure_ascii=False, default=str),
                                source_locator={
                                    "documentId": document.archive_id,
                                    "documentName": document.filename,
                                    "sheetName": sheet_name,
                                    "cellReference": cell.coordinate,
                                    "sourceType": "excel_cell",
                                },
                                metadata={"cells": [cell_data]},
                            )
                        )
                        if len(chunks) >= MAX_EXTRACTION_CHUNKS:
                            warnings.append("ADAPTER_CHUNK_LIMIT_REACHED")
                            break
                    if len(chunks) >= MAX_EXTRACTION_CHUNKS:
                        break
                if len(chunks) >= MAX_EXTRACTION_CHUNKS:
                    break
        finally:
            formulas.close()
            values.close()
        if formula_cell_count:
            warnings.append("FORMULA_CACHE_RECALCULATION_REQUIRED")
        if external_formula_reference_count:
            warnings.append("EXTERNAL_FORMULA_REFERENCE_BLOCKED")
        return ExtractionArtifact(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            document_id=document.archive_id,
            chunks=tuple(chunks),
            complete=not warnings,
            warnings=tuple(dict.fromkeys(warnings)),
            metadata={
                "sheetNames": formulas.sheetnames,
                "formulaAndCachedValuePreserved": True,
                "formulaCacheAuthoritative": False,
                "formulaCachePolicy": "deterministic_recalculation_required",
                "formulaCellCount": formula_cell_count,
                "cachedFormulaValueCount": cached_formula_value_count,
                "externalFormulaReferenceCount": external_formula_reference_count,
                "calculationProperties": calculation_properties,
                "visitedCellCount": visited_cells,
            },
        )


class DocxStructureAdapter:
    adapter_id = "docx_structure"
    adapter_version = "1.0.0"
    _word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    def supports(self, document: ArchivedDocument) -> bool:
        return document.extension == ".docx"

    def extract(self, document: ArchivedDocument) -> ExtractionArtifact:
        chunks: list[ExtractionChunk] = []
        with zipfile.ZipFile(io.BytesIO(document.content)) as package:
            info = package.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_DOCUMENT_XML_BYTES:
                raise ValueError("DOCX document.xml exceeds the structural adapter limit")
            root = ElementTree.fromstring(package.read(info))
        body = root.find(f"{{{self._word_ns}}}body")
        if body is None:
            raise ValueError("DOCX body is missing")
        paragraph_index = 0
        table_index = 0
        for node in list(body):
            if node.tag == f"{{{self._word_ns}}}p":
                paragraph_index += 1
                text = _word_text(node, self._word_ns)
                if text:
                    if len(text) > MAX_CHUNK_TEXT_CHARS:
                        raise ValueError("DOCX paragraph exceeds the structural adapter limit")
                    chunks.append(
                        ExtractionChunk(
                            chunk_id=f"paragraph-{paragraph_index}",
                            kind="docx_paragraph",
                            text=text,
                            source_locator={
                                "documentId": document.archive_id,
                                "documentName": document.filename,
                                "jsonPath": f"/word/document/body/paragraph/{paragraph_index}",
                                "sourceType": "document_paragraph",
                            },
                        )
                    )
                    if len(chunks) >= MAX_EXTRACTION_CHUNKS:
                        raise ValueError("DOCX chunk count exceeds the structural adapter limit")
            elif node.tag == f"{{{self._word_ns}}}tbl":
                table_index += 1
                for row_index, row in enumerate(node.findall(f"{{{self._word_ns}}}tr"), start=1):
                    values = [_word_text(cell, self._word_ns) for cell in row.findall(f"{{{self._word_ns}}}tc")]
                    if not any(values):
                        continue
                    chunks.append(
                        ExtractionChunk(
                            chunk_id=f"table-{table_index}-row-{row_index}",
                            kind="docx_table_row",
                            text="\t".join(values),
                            source_locator={
                                "documentId": document.archive_id,
                                "documentName": document.filename,
                                "jsonPath": f"/word/document/body/table/{table_index}/row/{row_index}",
                                "sourceType": "document_table_row",
                            },
                            metadata={"cells": values},
                        )
                    )
                    if len(chunks) >= MAX_EXTRACTION_CHUNKS:
                        raise ValueError("DOCX chunk count exceeds the structural adapter limit")
        return ExtractionArtifact(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            document_id=document.archive_id,
            chunks=tuple(chunks),
            metadata={"paragraphCount": paragraph_index, "tableCount": table_index},
        )


class TextStructureAdapter:
    adapter_id = "text_structure"
    adapter_version = "1.0.0"

    def supports(self, document: ArchivedDocument) -> bool:
        return document.extension in {".txt", ".csv", ".json"}

    def extract(self, document: ArchivedDocument) -> ExtractionArtifact:
        if len(document.content) > MAX_SOURCE_BYTES:
            raise ValueError("Text document exceeds the structural adapter limit")
        text, encoding = _decode_text(document.content)
        chunks: list[ExtractionChunk] = []
        if document.extension == ".csv":
            for row_index, row in enumerate(csv.reader(io.StringIO(text)), start=1):
                if len(chunks) >= MAX_EXTRACTION_CHUNKS:
                    raise ValueError("Delimited document row count exceeds the adapter limit")
                row_text = "\t".join(row)
                if len(row_text) > MAX_CHUNK_TEXT_CHARS:
                    raise ValueError("Delimited row exceeds the adapter limit")
                chunks.append(
                    ExtractionChunk(
                        chunk_id=f"row-{row_index}",
                        kind="csv_row",
                        text=row_text,
                        source_locator={
                            "documentId": document.archive_id,
                            "documentName": document.filename,
                            "jsonPath": f"/rows/{row_index}",
                            "sourceType": "delimited_row",
                        },
                    )
                )
        else:
            for line_index, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                if len(chunks) >= MAX_EXTRACTION_CHUNKS:
                    raise ValueError("Text line count exceeds the adapter limit")
                if len(line) > MAX_CHUNK_TEXT_CHARS:
                    raise ValueError("Text line exceeds the adapter limit")
                chunks.append(
                    ExtractionChunk(
                        chunk_id=f"line-{line_index}",
                        kind="text_line",
                        text=line,
                        source_locator={
                            "documentId": document.archive_id,
                            "documentName": document.filename,
                            "jsonPath": f"/lines/{line_index}",
                            "sourceType": "text_line",
                        },
                    )
                )
        return ExtractionArtifact(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            document_id=document.archive_id,
            chunks=tuple(chunks),
            metadata={"encoding": encoding},
        )


class DocumentAdapterRegistry:
    def __init__(self, adapters: Iterable[DocumentAdapter] | None = None):
        self._adapters: list[DocumentAdapter] = list(
            adapters
            or (
                PdfNativeAdapter(),
                XlsxStructureAdapter(),
                DocxStructureAdapter(),
                TextStructureAdapter(),
            )
        )

    def register(self, adapter: DocumentAdapter, *, prepend: bool = False) -> None:
        if any(item.adapter_id == adapter.adapter_id for item in self._adapters):
            raise ValueError(f"Adapter already registered: {adapter.adapter_id}")
        if prepend:
            self._adapters.insert(0, adapter)
        else:
            self._adapters.append(adapter)

    def resolve(self, document: ArchivedDocument) -> DocumentAdapter:
        for adapter in self._adapters:
            if adapter.supports(document):
                return adapter
        raise LookupError(f"No isolated document adapter is registered for {document.extension or document.content_type}")

    def manifests(self) -> list[dict[str, str]]:
        return [
            {"adapterId": item.adapter_id, "adapterVersion": item.adapter_version}
            for item in self._adapters
        ]


def detect_document_instruction_text(chunks: Iterable[ExtractionChunk]) -> list[InspectionFinding]:
    """Flag likely prompt-injection text without treating document text as an instruction."""

    patterns = (
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"system\s+prompt",
        r"developer\s+message",
        r"이전\s*(지시|명령).*무시",
        r"시스템\s*프롬프트",
    )
    findings: list[InspectionFinding] = []
    for chunk in chunks:
        if any(re.search(pattern, chunk.text, flags=re.IGNORECASE) for pattern in patterns):
            findings.append(
                InspectionFinding(
                    "DOCUMENT_INSTRUCTION_LIKE_TEXT",
                    "high",
                    "Instruction-like text was found inside an untrusted source document and must not control the parser.",
                    False,
                    {"chunkId": chunk.chunk_id, "sourceLocator": chunk.source_locator},
                )
            )
    return findings


def _inspect_zip_container(content: bytes) -> tuple[list[InspectionFinding], dict[str, Any]]:
    findings: list[InspectionFinding] = []
    metadata: dict[str, Any] = {}
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return [InspectionFinding("INVALID_OFFICE_CONTAINER", "critical", "The Office ZIP container is invalid.", True)], metadata
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        infos = archive.infolist()
        total_uncompressed = sum(item.file_size for item in infos)
        total_compressed = sum(max(item.compress_size, 1) for item in infos)
        ratio = total_uncompressed / max(total_compressed, 1)
        metadata.update(
            {
                "zipEntryCount": len(infos),
                "zipUncompressedBytes": total_uncompressed,
                "zipCompressionRatio": round(ratio, 3),
            }
        )
        expansion_blocked = (
            len(infos) > MAX_ZIP_ENTRIES
            or total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES
            or ratio > MAX_ZIP_RATIO
            or any(item.file_size > MAX_ZIP_ENTRY_BYTES for item in infos)
        )
        if expansion_blocked:
            findings.append(
                InspectionFinding(
                    "ARCHIVE_EXPANSION_LIMIT_EXCEEDED",
                    "critical",
                    "The Office container exceeds safe archive expansion limits.",
                    True,
                )
            )
        if any(_unsafe_zip_name(item.filename) for item in infos):
            findings.append(InspectionFinding("UNSAFE_ARCHIVE_PATH", "critical", "Unsafe path found inside Office container.", True))
        if any(item.flag_bits & 0x1 for item in infos):
            findings.append(InspectionFinding("ENCRYPTED_ARCHIVE_ENTRY", "critical", "Encrypted Office entry found.", True))
        names = {item.filename.lower() for item in infos}
        if any(name.endswith("vbaproject.bin") for name in names):
            findings.append(InspectionFinding("VBA_PROJECT_PRESENT", "critical", "Embedded VBA project found.", True))
        if any("externallinks/" in name for name in names):
            findings.append(
                InspectionFinding(
                    "EXTERNAL_LINK_PRESENT",
                    "high",
                    "External workbook links were detected; the parser will not resolve or execute them.",
                    False,
                )
            )
        if expansion_blocked:
            return findings, metadata
        relationship_files = [
            item
            for item in infos
            if item.filename.lower().endswith(".rels")
            and item.file_size <= MAX_RELATIONSHIP_ENTRY_BYTES
        ]
        for item in relationship_files:
            try:
                raw = archive.read(item)
            except Exception:
                continue
            if b'TargetMode="External"' in raw or b"TargetMode='External'" in raw:
                findings.append(
                    InspectionFinding(
                        "EXTERNAL_RELATIONSHIP_PRESENT",
                        "high",
                        "External Office relationship detected; network resolution is prohibited.",
                        False,
                        {"entry": item.filename},
                    )
                )
    return findings, metadata


def inspect_office_container_safety(content: bytes) -> tuple[bool, list[InspectionFinding], dict[str, Any]]:
    """Expose the parser's bounded Office-container gate to read-only viewers.

    Preview code must enforce the same ZIP entry, expansion, encryption, and
    unsafe-path rules as ingestion before an OpenXML library expands entries.
    """

    findings, metadata = _inspect_zip_container(content)
    return not any(item.blocking for item in findings), findings, metadata


def _magic_mismatch(document: ArchivedDocument) -> InspectionFinding | None:
    extension = document.extension
    content = document.content
    if extension == ".pdf" and content and not content.startswith(b"%PDF"):
        return InspectionFinding("FILE_SIGNATURE_MISMATCH", "critical", "PDF extension does not match file signature.", True)
    if extension in {".xlsx", ".xlsm", ".docx", ".docm", ".hwpx"} and content and not content.startswith(b"PK"):
        return InspectionFinding("FILE_SIGNATURE_MISMATCH", "critical", "Office extension does not match ZIP signature.", True)
    return None


def _media_family(document: ArchivedDocument) -> str:
    if document.extension == ".pdf" or document.content_type == "application/pdf":
        return "pdf"
    if document.extension in {".xlsx", ".xlsm"}:
        return "xlsx"
    if document.extension in {".docx", ".docm"}:
        return "docx"
    if document.extension in {".txt", ".csv", ".json"}:
        return "text"
    return "legacy_or_unknown"


def _evidence_id(document_version_id: str, chunk_id: str) -> str:
    material = f"{document_version_id}|{chunk_id}".encode("utf-8")
    return "parser-evidence:" + hashlib.sha256(material).hexdigest()[:32]


def _unsafe_zip_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return normalized.startswith("/") or any(part == ".." for part in normalized.split("/"))


def _word_text(node: ElementTree.Element, namespace: str) -> str:
    values = [item.text or "" for item in node.iter(f"{{{namespace}}}t")]
    return "".join(values).strip()


def _decode_text(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace"), "utf-8-replacement"


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", value.strip()).strip("-")
    return normalized[:80] or "sheet"
