"""Deterministic source-document preview helpers.

The caller must authorize and tenant-scope the project before invoking these
helpers.  This module never resolves filesystem paths supplied by a client; it
only operates on bytes already resolved and integrity-checked by the upload
archive repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from io import BytesIO
from typing import Any, Dict


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
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                alpha=False,
                colorspace=fitz.csRGB,
            )
            return {
                "content": pixmap.tobytes("png"),
                "pageNumber": page_number,
                "pageCount": document.page_count,
                "pageWidth": float(page_rect.width),
                "pageHeight": float(page_rect.height),
                "pixelWidth": int(pixmap.width),
                "pixelHeight": int(pixmap.height),
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

    import openpyxl
    from openpyxl.utils import get_column_letter, range_boundaries
    from parsing_platform.adapters import inspect_office_container_safety

    normalized_sheet = str(sheet_name or "").strip()
    normalized_reference = str(cell_reference or "").strip()
    if not normalized_sheet or not normalized_reference:
        raise ValueError("sheet_name and cell_reference are required")
    if len(normalized_sheet) > 255 or len(normalized_reference) > 64:
        raise ValueError("workbook locator is too long")
    if not 0 <= context_rows <= 10 or not 0 <= context_columns <= 10:
        raise ValueError("workbook preview context is outside the allowed bound")
    try:
        min_column, min_row, max_column, max_row = range_boundaries(normalized_reference)
    except (TypeError, ValueError) as exc:
        raise ValueError("cell_reference must be an A1 cell or range") from exc
    focus_cells = (max_row - min_row + 1) * (max_column - min_column + 1)
    if focus_cells <= 0 or focus_cells > max_cells:
        raise ValueError("focused workbook range is too large")
    if max_row > 10_000 or max_column > 512:
        raise ValueError("workbook locator is outside the controlled preview region")

    preview_min_row = max(1, min_row - context_rows)
    preview_max_row = max_row + context_rows
    preview_min_column = max(1, min_column - context_columns)
    preview_max_column = max_column + context_columns
    preview_cells = (
        (preview_max_row - preview_min_row + 1)
        * (preview_max_column - preview_min_column + 1)
    )
    if preview_cells > max_cells:
        raise ValueError("workbook preview window is too large")

    safe_container, findings, _ = inspect_office_container_safety(content)
    if not safe_container:
        finding_codes = ",".join(sorted({item.code for item in findings if item.blocking}))
        raise ValueError(
            f"workbook container failed bounded safety inspection: {finding_codes}"
        )

    formula_book = None
    value_book = None
    try:
        formula_book = openpyxl.load_workbook(
            BytesIO(content),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
        value_book = openpyxl.load_workbook(
            BytesIO(content),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
    except Exception as exc:
        if formula_book is not None:
            formula_book.close()
        if value_book is not None:
            value_book.close()
        raise ValueError("source document is not a readable XLSX workbook") from exc

    try:
        if normalized_sheet not in formula_book.sheetnames:
            raise KeyError("workbook sheet was not found")
        formula_sheet = formula_book[normalized_sheet]
        value_sheet = value_book[normalized_sheet]
        rows = []
        for row_number in range(preview_min_row, preview_max_row + 1):
            cells = []
            for column_number in range(preview_min_column, preview_max_column + 1):
                formula_cell = formula_sheet.cell(row=row_number, column=column_number)
                value_cell = value_sheet.cell(row=row_number, column=column_number)
                formula = (
                    str(formula_cell.value)
                    if isinstance(formula_cell.value, str)
                    and formula_cell.value.startswith("=")
                    else None
                )
                cells.append(
                    {
                        "coordinate": f"{get_column_letter(column_number)}{row_number}",
                        "rowNumber": row_number,
                        "columnNumber": column_number,
                        "displayValue": _json_cell_value(value_cell.value if formula else formula_cell.value),
                        "formula": formula,
                        "dataType": str(formula_cell.data_type or "n"),
                        "focused": (
                            min_row <= row_number <= max_row
                            and min_column <= column_number <= max_column
                        ),
                    }
                )
            rows.append({"rowNumber": row_number, "cells": cells})
        return {
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
        }
    finally:
        formula_book.close()
        value_book.close()


def _json_cell_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)
