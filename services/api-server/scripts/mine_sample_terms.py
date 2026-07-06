from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from use_cases.glossary import list_glossary_terms_use_case, normalize_glossary_surfaces_use_case
from use_cases.sample_term_mining import TextSource, build_sample_term_mining_package


SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".md", ".json"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mine local REALYTICS sample documents for glossary term surfaces."
    )
    parser.add_argument(
        "--sample-dir",
        default=str(Path(__file__).resolve().parents[3].parent / "sample"),
        help="Folder containing sample PF/development documents.",
    )
    parser.add_argument("--output", default="", help="Optional JSON output path.")
    parser.add_argument("--limit-files", type=int, default=0, help="Optional maximum file count.")
    parser.add_argument("--max-items-per-source", type=int, default=500)
    parser.add_argument("--persist-candidates", action="store_true", help="Persist unresolved terms to glossary candidate queue.")
    parser.add_argument("--project-id", default="sample-corpus")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()

    sample_dir = Path(args.sample_dir)
    if not sample_dir.exists():
        raise SystemExit(f"Sample directory not found: {sample_dir}")

    run_id = args.run_id or "sample-term-mining-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    sources = _load_sources(sample_dir, limit_files=args.limit_files)
    glossary_terms = list_glossary_terms_use_case()["terms"]
    package = build_sample_term_mining_package(
        sources,
        glossary_terms,
        project_id=args.project_id,
        run_id=run_id,
        max_items_per_source=args.max_items_per_source,
    )
    package["sampleDirectory"] = str(sample_dir)

    if args.persist_candidates:
        package["candidatePersistence"] = _persist_candidates(package, project_id=args.project_id, run_id=run_id)
    else:
        package["candidatePersistence"] = {
            "enabled": False,
            "reason": "Dry run. Use --persist-candidates to write unresolved surfaces to the glossary candidate queue.",
        }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(_console_summary(package), ensure_ascii=False, indent=2))
    return 0


def _load_sources(sample_dir: Path, *, limit_files: int = 0) -> List[TextSource]:
    paths = [
        path
        for path in sorted(sample_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    if limit_files > 0:
        paths = paths[:limit_files]
    sources: List[TextSource] = []
    for path in paths:
        text = _read_text(path)
        if not text.strip():
            continue
        sources.append(
            TextSource(
                source_name=path.name,
                text=text,
                source_type=path.suffix.lower().lstrip("."),
                source_locator={
                    "fileName": path.name,
                    "filePath": str(path),
                },
            )
        )
    return sources


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return _read_pdf(path)
        if suffix == ".docx":
            return _read_docx(path)
        if suffix in {".xlsx", ".xlsm"}:
            return _read_xlsx(path)
        if suffix == ".xls":
            return _read_xls(path)
        if suffix in {".csv", ".txt", ".md", ".json"}:
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:  # pragma: no cover - script diagnostic path
        return f"READ_ERROR {path.name}: {exc}"
    return ""


def _read_pdf(path: Path) -> str:
    import fitz  # type: ignore

    parts: List[str] = []
    with fitz.open(str(path)) as doc:
        for page_index, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            if text.strip():
                parts.append(f"[page {page_index}]\n{text}")
    return "\n".join(parts)


def _read_docx(path: Path) -> str:
    from docx import Document  # type: ignore

    document = Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table_index, table in enumerate(document.tables, start=1):
        for row_index, row in enumerate(table.rows, start=1):
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(f"[table {table_index} row {row_index}] " + " | ".join(cells))
    return "\n".join(parts)


def _read_xlsx(path: Path) -> str:
    from openpyxl import load_workbook  # type: ignore

    workbook = load_workbook(str(path), read_only=True, data_only=True)
    parts: List[str] = []
    for sheet in workbook.worksheets:
        parts.append(f"[sheet] {sheet.title}")
        for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            values = [str(value).strip() for value in row if value not in (None, "")]
            if values:
                parts.append(f"[{sheet.title}!{row_index}] " + " | ".join(values))
    return "\n".join(parts)


def _read_xls(path: Path) -> str:
    import xlrd  # type: ignore

    book = xlrd.open_workbook(str(path))
    parts: List[str] = []
    for sheet in book.sheets():
        parts.append(f"[sheet] {sheet.name}")
        for row_index in range(sheet.nrows):
            values = [str(value).strip() for value in sheet.row_values(row_index) if str(value).strip()]
            if values:
                parts.append(f"[{sheet.name}!{row_index + 1}] " + " | ".join(values))
    return "\n".join(parts)


def _persist_candidates(package: Dict[str, Any], *, project_id: str, run_id: str) -> Dict[str, Any]:
    candidate_items = [
        {
            "surface": result.get("surface"),
            "sourceField": result.get("sourceField"),
            "sourceLocator": result.get("sourceLocator"),
            "context": result.get("contextExcerpt"),
            "category": (result.get("suggestedTerm") or {}).get("category"),
        }
        for result in package.get("results") or []
        if result.get("action") != "matched"
    ]
    normalized = normalize_glossary_surfaces_use_case(
        candidate_items,
        project_id=project_id,
        run_id=run_id,
    )
    return {
        "enabled": True,
        "inputCount": len(candidate_items),
        "candidateCount": normalized.get("candidateCount"),
        "matchedCount": normalized.get("matchedCount"),
        "schemaVersion": normalized.get("schemaVersion"),
    }


def _console_summary(package: Dict[str, Any]) -> Dict[str, Any]:
    results = package.get("results") or []
    return {
        "schemaVersion": package.get("schemaVersion"),
        "sampleDirectory": package.get("sampleDirectory"),
        "sourceCount": package.get("sourceCount"),
        "surfaceCount": package.get("surfaceCount"),
        "summary": package.get("summary"),
        "candidatePersistence": package.get("candidatePersistence"),
        "topReviewItems": [
            {
                "surface": item.get("surface"),
                "action": item.get("action"),
                "suggestedTerm": (item.get("suggestedTerm") or {}).get("termId"),
                "confidence": item.get("confidence"),
                "sourceName": item.get("sourceName"),
            }
            for item in results
            if item.get("action") != "matched"
        ][:20],
    }


if __name__ == "__main__":
    raise SystemExit(main())
