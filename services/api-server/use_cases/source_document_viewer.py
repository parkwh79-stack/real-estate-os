"""Deterministic source-document preview helpers.

The caller must authorize and tenant-scope the project before invoking these
helpers.  This module never resolves filesystem paths supplied by a client; it
only operates on bytes already resolved and integrity-checked by the upload
archive repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from io import BytesIO
import math
import json
from typing import Any, Dict

MAX_PDF_PREVIEW_BYTES = 3 * 1024 * 1024
MAX_PDF_PREVIEW_RENDER_PIXELS = 3_000_000
WORKBOOK_PREVIEW_LIMITS = {"maxRows": 10_000, "maxColumns": 512, "maxCells": 400, "maxSheets": 100}
MAX_WORKBOOK_PREVIEW_TEXT = 1024
MAX_WORKBOOK_PREVIEW_BYTES = 3 * 1024 * 1024


@contextmanager
def _preview_workbooks(content: bytes, *, cached: bool = False):
    import openpyxl
    from parsing_platform.adapters import MAX_SOURCE_BYTES, inspect_office_container_safety

    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError("workbook source exceeds the controlled preview size")
    safe_container, findings, _ = inspect_office_container_safety(content)
    if not safe_container:
        codes = ",".join(sorted({item.code for item in findings if item.blocking}))
        raise ValueError(f"workbook container failed bounded safety inspection: {codes}")
    formula_book = value_book = None
    try:
        try:
            formula_book = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
            if cached:
                value_book = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
        except Exception as exc:
            raise ValueError("source document is not a readable XLSX workbook") from exc
        try:
            yield formula_book, value_book, [item.code for item in findings if not item.blocking]
        except (KeyError, ValueError):
            raise
        except Exception as exc:
            raise ValueError("source workbook could not be read within the bounded preview") from exc
    finally:
        if formula_book is not None:
            formula_book.close()
        if value_book is not None:
            value_book.close()


def build_workbook_sheet_preview(content: bytes) -> Dict[str, Any]:
    """List real sheets and a bounded initial window, independent of Parser facts."""
    from openpyxl.utils import get_column_letter

    with _preview_workbooks(content) as (workbook, _values, inspection_warnings):
        sheets = []
        warnings = ["원본의 저장값과 수식만 표시합니다. 수식을 실행하거나 분석 입력을 승인하지 않습니다."]
        for index, sheet in enumerate(workbook.worksheets[:WORKBOOK_PREVIEW_LIMITS["maxSheets"]]):
            declared_rows, declared_columns = sheet.max_row, sheet.max_column
            row_count = min(declared_rows or WORKBOOK_PREVIEW_LIMITS["maxRows"], WORKBOOK_PREVIEW_LIMITS["maxRows"])
            column_count = min(declared_columns or WORKBOOK_PREVIEW_LIMITS["maxColumns"], WORKBOOK_PREVIEW_LIMITS["maxColumns"])
            sheets.append({"sheetName": sheet.title, "sheetIndex": index, "state": sheet.sheet_state,
                           "rowCount": row_count, "columnCount": column_count,
                           "declaredRowCount": declared_rows, "declaredColumnCount": declared_columns,
                           "dimensionsTruncated": declared_rows is None or declared_columns is None
                           or declared_rows > row_count or declared_columns > column_count})
        if not sheets:
            raise ValueError("source workbook has no readable worksheets")
        selected = next((sheet for sheet in sheets if sheet["state"] == "visible"), sheets[0])
        sheet = workbook[selected["sheetName"]]
        first = None
        # Formatting can make max_row a million. Never scan that declared range
        # to decide whether a preview exists or to discover a first screen.
        for row_number, cells in enumerate(sheet.iter_rows(min_row=1, max_row=min(200, selected["rowCount"]),
                                                           min_col=1, max_col=min(32, selected["columnCount"])), 1):
            for column_number, cell in enumerate(cells, 1):
                value = cell.value
                if value is not None and (not isinstance(value, str) or value.strip()):
                    first = (row_number, column_number)
                    break
            if first is not None:
                break
        default_range = None
        if first is not None:
            row, column = first
            default_range = (f"{get_column_letter(column)}{row}:"
                             f"{get_column_letter(min(column + 19, selected['columnCount']))}{min(row + 19, selected['rowCount'])}")
        else:
            warnings.append("앞 200행·32열의 제한 탐색에서 내용이 확인되지 않았습니다. 다른 시트나 범위를 선택하세요.")
        truncated = len(workbook.worksheets) > len(sheets) or any(sheet["dimensionsTruncated"] for sheet in sheets)
        if truncated:
            warnings.append("큰 시트의 탐색 범위 또는 시트 목록이 제한되어 있습니다. 전체 원본은 다운로드로 확인하세요.")
        if inspection_warnings:
            warnings.append("외부 참조가 포함될 수 있습니다. 외부 문서·주소를 열거나 수식을 실행하지 않습니다.")
        return {"schemaVersion": "realytics-workbook-original-sheets-v1", "sheets": sheets,
                "sheetCount": len(workbook.worksheets), "limits": dict(WORKBOOK_PREVIEW_LIMITS),
                "defaultSheetName": selected["sheetName"], "defaultRange": default_range,
                "truncated": truncated, "warnings": warnings, "analysisOnly": True, "approvalEligible": False,
                "formulaPolicy": "display_only_never_executed"}


class UnsatisfiableByteRange(ValueError):
    """Raised when a single RFC byte range cannot be served."""


@dataclass(frozen=True)
class ResolvedByteRange:
    start: int
    end: int
    size: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def content_range(self) -> str:
        return f"bytes {self.start}-{self.end}/{self.size}"


def parse_single_byte_range(value: str | None, size: int) -> ResolvedByteRange | None:
    """Parse one ``Range: bytes=...`` request.

    Multipart ranges are deliberately rejected.  They add complex response
    framing without improving the PDF viewer path, and accepting a partially
    parsed multi-range header would be ambiguous and unsafe.
    """

    if not value:
        return None
    if size < 0:
        raise UnsatisfiableByteRange("invalid resource size")
    unit, separator, raw_spec = value.strip().partition("=")
    if not separator or unit.strip().lower() != "bytes":
        raise UnsatisfiableByteRange("only byte ranges are supported")
    spec = raw_spec.strip()
    if not spec or "," in spec or spec.count("-") != 1:
        raise UnsatisfiableByteRange("exactly one byte range is supported")
    raw_start, raw_end = (part.strip() for part in spec.split("-", 1))
    if not raw_start:
        if not raw_end.isdigit():
            raise UnsatisfiableByteRange("invalid suffix byte range")
        suffix_length = int(raw_end)
        if suffix_length <= 0 or size <= 0:
            raise UnsatisfiableByteRange("suffix byte range is empty")
        start = max(0, size - suffix_length)
        return ResolvedByteRange(start=start, end=size - 1, size=size)
    if not raw_start.isdigit() or (raw_end and not raw_end.isdigit()):
        raise UnsatisfiableByteRange("invalid byte range")
    start = int(raw_start)
    if start >= size or size <= 0:
        raise UnsatisfiableByteRange("byte range starts after the resource")
    end = int(raw_end) if raw_end else size - 1
    if end < start:
        raise UnsatisfiableByteRange("byte range end precedes start")
    return ResolvedByteRange(start=start, end=min(end, size - 1), size=size)


def render_pdf_page_preview(
    content: bytes,
    *,
    page_number: int,
    scale: float = 1.5,
) -> Dict[str, Any]:
    """Render one real PDF page and return its page-space geometry."""

    import fitz  # PyMuPDF; already used by the authoritative parsing path.
    from parsing_platform.adapters import MAX_PDF_PAGES

    if page_number < 1:
        raise ValueError("page_number must be one-based")
    if not 0.5 <= scale <= 2.0:
        raise ValueError("scale must be between 0.5 and 2.0")
    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if document.page_count > MAX_PDF_PAGES:
                raise ValueError("PDF page count exceeds the controlled preview limit")
            if page_number > document.page_count:
                raise IndexError("PDF page is outside the document")
            page = document.load_page(page_number - 1)
            page_rect = page.rect
            pixel_width = int(page_rect.width * scale + 0.5)
            pixel_height = int(page_rect.height * scale + 0.5)
            if (
                pixel_width <= 0
                or pixel_height <= 0
                or pixel_width * pixel_height > 25_000_000
            ):
                raise ValueError("PDF page dimensions exceed the controlled preview limit")
            # Preview bytes also traverse the Vercel BFF. Keep a display image
            # bounded independently of the full-resolution, internal OCR image.
            pixel_scale = min(scale, math.sqrt(MAX_PDF_PREVIEW_RENDER_PIXELS / (page_rect.width * page_rect.height)))
            for _ in range(4):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(pixel_scale, pixel_scale),
                    alpha=False, colorspace=fitz.csRGB)
                image = pixmap.tobytes("png")
                if len(image) <= MAX_PDF_PREVIEW_BYTES:
                    break
                pixel_scale *= math.sqrt(MAX_PDF_PREVIEW_BYTES / len(image)) * 0.95
            else:
                raise ValueError("PDF preview exceeds the response size limit")
            return {
                "content": image,
                "pageNumber": page_number,
                "pageCount": document.page_count,
                "pageWidth": float(page_rect.width),
                "pageHeight": float(page_rect.height),
                "pixelWidth": int(pixmap.width),
                "pixelHeight": int(pixmap.height),
                "pixelScale": pixel_scale,
                "previewAdjusted": pixel_scale < scale,
                "coordinateSpace": "pdf_points_top_left",
            }
    except (IndexError, ValueError):
        raise
    except Exception as exc:
        raise ValueError("source document is not a readable PDF") from exc


def build_workbook_cell_preview(
    content: bytes,
    *,
    sheet_name: str,
    cell_reference: str,
    context_rows: int = 3,
    context_columns: int = 3,
    max_cells: int = 400,
) -> Dict[str, Any]:
    """Read a bounded, non-executing Excel window around an exact locator."""

    from openpyxl.utils import get_column_letter, range_boundaries
    from openpyxl.worksheet.formula import ArrayFormula

    normalized_sheet = str(sheet_name or "")
    normalized_reference = str(cell_reference or "").strip()
    if not normalized_sheet.strip() or not normalized_reference:
        raise ValueError("sheet_name and cell_reference are required")
    if len(normalized_sheet) > 255 or len(normalized_reference) > 64:
        raise ValueError("workbook locator is too long")
    if not 0 <= context_rows <= 10 or not 0 <= context_columns <= 10:
        raise ValueError("workbook preview context is outside the allowed bound")
    try:
        min_column, min_row, max_column, max_row = range_boundaries(normalized_reference)
    except (TypeError, ValueError) as exc:
        raise ValueError("cell_reference must be an A1 cell or range") from exc
    if any(not isinstance(value, int) or value < 1 for value in (min_column, min_row, max_column, max_row)):
        raise ValueError("cell_reference must contain bounded rows and columns")
    if min_row > max_row or min_column > max_column:
        raise ValueError("cell_reference boundaries must be ordered")
    if not 1 <= max_cells <= WORKBOOK_PREVIEW_LIMITS["maxCells"]:
        raise ValueError("workbook preview cell limit is invalid")
    focus_cells = (max_row - min_row + 1) * (max_column - min_column + 1)
    if focus_cells <= 0 or focus_cells > max_cells:
        raise ValueError("focused workbook range is too large")
    if max_row > 10_000 or max_column > 512:
        raise ValueError("workbook locator is outside the controlled preview region")

    preview_min_row = max(1, min_row - context_rows)
    preview_max_row = min(WORKBOOK_PREVIEW_LIMITS["maxRows"], max_row + context_rows)
    preview_min_column = max(1, min_column - context_columns)
    preview_max_column = min(WORKBOOK_PREVIEW_LIMITS["maxColumns"], max_column + context_columns)
    preview_cells = (
        (preview_max_row - preview_min_row + 1)
        * (preview_max_column - preview_min_column + 1)
    )
    if preview_cells > max_cells:
        raise ValueError("workbook preview window is too large")

    with _preview_workbooks(content, cached=True) as (formula_book, value_book, inspection_warnings):
        if normalized_sheet not in formula_book.sheetnames:
            raise KeyError("workbook sheet was not found")
        formula_sheet = formula_book[normalized_sheet]
        value_sheet = value_book[normalized_sheet]
        rows = []
        warnings = ["수식은 실행하지 않습니다. 수식의 값은 원본에 저장된 캐시이며 최신 계산값이 아닐 수 있습니다."]
        if inspection_warnings:
            warnings.append("외부 참조는 표시만 하며 외부 파일·주소를 열지 않습니다.")
        window = {"min_row": preview_min_row, "max_row": preview_max_row,
                  "min_col": preview_min_column, "max_col": preview_max_column}
        formula_rows = formula_sheet.iter_rows(**window)
        value_rows = value_sheet.iter_rows(**window)
        any_text_truncated = False
        for row_number, (formula_row, value_row) in enumerate(zip(formula_rows, value_rows, strict=True), preview_min_row):
            cells = []
            for column_number, (formula_cell, value_cell) in enumerate(zip(formula_row, value_row, strict=True), preview_min_column):
                is_formula = formula_cell.data_type == "f"
                formula_text = (
                    formula_cell.value.text
                    if isinstance(formula_cell.value, ArrayFormula)
                    else formula_cell.value
                )
                formula = (
                    formula_text
                    if is_formula and isinstance(formula_text, str)
                    else None
                )
                raw_value = _json_cell_value(formula_text)
                cached_value = _json_cell_value(value_cell.value) if is_formula else None
                display_value = cached_value if is_formula else raw_value
                if is_formula:
                    value_state = "formula_cache_error" if value_cell.data_type == "e" else "formula_cache_missing" if value_cell.value is None else "formula_cached"
                else:
                    value_state = "empty" if formula_cell.value is None else "error" if formula_cell.data_type == "e" else "literal"
                if isinstance(value_cell.value if is_formula else formula_cell.value, float) and display_value is None:
                    value_state = "error"
                source_values = {"rawValue": raw_value, "cachedValue": cached_value,
                                 "displayValue": display_value, "formula": formula,
                                 "numberFormat": str(getattr(formula_cell, "number_format", None) or "General")}
                text_truncated = any(isinstance(value, str) and len(value) > MAX_WORKBOOK_PREVIEW_TEXT for value in source_values.values())
                any_text_truncated = any_text_truncated or text_truncated
                source_values = {key: value[:MAX_WORKBOOK_PREVIEW_TEXT] if isinstance(value, str) else value
                                 for key, value in source_values.items()}
                cells.append(
                    {
                        "coordinate": f"{get_column_letter(column_number)}{row_number}",
                        "rowNumber": row_number,
                        "columnNumber": column_number,
                        **source_values,
                        "valueState": value_state,
                        "valueTruncated": text_truncated,
                        "dataType": str(formula_cell.data_type or "n"),
                        "focused": (
                            min_row <= row_number <= max_row
                            and min_column <= column_number <= max_column
                        ),
                    }
                )
            rows.append({"rowNumber": row_number, "cells": cells})
        preview = {
            "schemaVersion": "realytics-workbook-evidence-preview-v1",
            "sheetName": normalized_sheet,
            "focusRange": normalized_reference,
            "previewRange": (
                f"{get_column_letter(preview_min_column)}{preview_min_row}:"
                f"{get_column_letter(preview_max_column)}{preview_max_row}"
            ),
            "columns": [
                {
                    "columnNumber": column_number,
                    "label": get_column_letter(column_number),
                }
                for column_number in range(preview_min_column, preview_max_column + 1)
            ],
            "rows": rows,
            "formulaPolicy": "display_only_never_executed",
            "limits": dict(WORKBOOK_PREVIEW_LIMITS), "analysisOnly": True, "approvalEligible": False,
            "truncated": any_text_truncated, "warnings": warnings,
        }
        if len(json.dumps(preview, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_WORKBOOK_PREVIEW_BYTES:
            for row in rows:
                for cell in row["cells"]:
                    for key in ("rawValue", "cachedValue", "displayValue", "formula", "numberFormat"):
                        if isinstance(cell[key], str) and len(cell[key]) > 256:
                            cell[key] = cell[key][:256]
                            cell["valueTruncated"] = True
            preview["truncated"] = True
        if preview["truncated"]:
            warnings.append("긴 셀 내용은 미리보기 크기 제한으로 잘렸습니다. 전체 값은 원본 다운로드로 확인하세요.")
        if len(json.dumps(preview, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_WORKBOOK_PREVIEW_BYTES:
            raise ValueError("workbook preview response exceeds its size limit; select a smaller range")
        return preview


def _json_cell_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)
