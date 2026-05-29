from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = API_ROOT / "data" / "fixtures" / "pilot_project_input_package_template.json"
DEFAULT_WORKSPACE = API_ROOT / "data" / "runtime" / "pilot-input"

DOCUMENT_TYPE_RULES = [
    ("land_ledger", ["토지조서", "land ledger"]),
    ("feasibility_workbook", ["사업수지", "feasibility", "pf검토"]),
    ("pf_review_case", ["하나금융투자", "pf"]),
    ("logistics_reference", ["물류센터", "lg전자"]),
    ("investment_review_reference", ["투자심사", "투자심사위원회"]),
    ("development_guide", ["개발 업무", "업무 총론"]),
    ("lodging_reference", ["생활형숙박시설", "충무로"]),
    ("market_reference", ["시장 동향", "market"]),
]


def classify_document(path: Path) -> str:
    name = path.name.lower()
    for document_type, tokens in DOCUMENT_TYPE_RULES:
        if any(token.lower() in name for token in tokens):
            return document_type
    return "source_document"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_source_files(sample_dir: Path | None) -> Iterable[Path]:
    if not sample_dir or not sample_dir.exists():
        return []
    allowed_suffixes = {".pdf", ".docx", ".xlsx", ".xls", ".csv"}
    return sorted(
        path
        for path in sample_dir.iterdir()
        if path.is_file() and path.suffix.lower() in allowed_suffixes
    )


def build_manifest(sample_dir: Path | None, copy_samples: bool, documents_dir: Path) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    for path in iter_source_files(sample_dir):
        target_path = None
        if copy_samples:
            documents_dir.mkdir(parents=True, exist_ok=True)
            target_path = documents_dir / path.name
            if not target_path.exists():
                shutil.copy2(path, target_path)
        manifest_path = target_path or path
        records.append({
            "documentType": classify_document(path),
            "originalFileName": path.name,
            "sourcePath": str(path),
            "workspacePath": str(target_path) if target_path else None,
            "fileSize": path.stat().st_size,
            "sha256": sha256_file(path),
            "status": "copied_to_workspace" if target_path else "referenced_external_file",
            "requiredForPilot": classify_document(path) in {
                "land_ledger",
                "feasibility_workbook",
                "pf_review_case",
            },
            "reviewNotes": "",
            "locatorPlan": {
                "pdf": "Record page/table/paragraph numbers for extracted claims.",
                "excel": "Record sheet/cell/range for numeric assumptions.",
                "docx": "Record section/table/page after render if used as evidence.",
            }.get(manifest_path.suffix.lower().lstrip("."), "Record field-level source locator before approval."),
        })

    return {
        "schemaVersion": "realytics-pilot-source-manifest-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sampleDir": str(sample_dir) if sample_dir else None,
        "copySamples": copy_samples,
        "documentCount": len(records),
        "documents": records,
    }


def write_readme(workspace: Path, sample_dir: Path | None, copy_samples: bool) -> None:
    readme = f"""# Pilot Input Workspace

Generated: {datetime.now(timezone.utc).isoformat()}

This folder is outside the Git repository and is intended for real pilot materials.

## Files

- `pilot_input_payload.json`: editable payload copied from the pilot template.
- `source_document_manifest.json`: source document inventory with SHA-256 hashes.
- `outputs/`: recommended destination for pilot summary and full run JSON.
- `documents/`: populated only when `--copy-samples` is used.

## Source Mode

- Sample directory: `{sample_dir or ""}`
- Copy samples: `{copy_samples}`

If `copySamples=false`, source documents remain in their original location and only paths/hashes are stored.

## Recommended Next Command

```powershell
cd "{API_ROOT}"
python scripts/run_pilot_fixture.py --input "{workspace / "pilot_input_payload.json"}" --output "{workspace / "outputs" / "pilot-summary.json"}" --full-run-output "{workspace / "outputs" / "pilot-full-run.json"}"
```

## Pilot Checklist

1. Replace template project values with the actual anonymized project.
2. Confirm land PNU, official land price base year/source, and registry evidence.
3. Confirm feasibility totals reconcile with product mix revenue and cost items.
4. Add at least two market comparables with source locators.
5. Confirm PF tranche amount, priority, interest rate, and fee rate.
6. Run the pilot CLI and review `approvalReadinessGate.requiredActions`.
"""
    (workspace / "README.md").write_text(readme, encoding="utf-8")


def prepare_workspace(
    workspace: Path,
    sample_dir: Path | None,
    template: Path,
    copy_samples: bool,
    overwrite: bool,
) -> Dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "outputs").mkdir(exist_ok=True)
    documents_dir = workspace / "documents"

    payload_path = workspace / "pilot_input_payload.json"
    if payload_path.exists() and not overwrite:
        raise FileExistsError(f"{payload_path} already exists. Use --overwrite to replace it.")
    shutil.copy2(template, payload_path)

    manifest = build_manifest(sample_dir, copy_samples, documents_dir)
    (workspace / "source_document_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_readme(workspace, sample_dir, copy_samples)

    return {
        "schemaVersion": "realytics-pilot-workspace-summary-v1",
        "workspace": str(workspace),
        "payloadPath": str(payload_path),
        "manifestPath": str(workspace / "source_document_manifest.json"),
        "readmePath": str(workspace / "README.md"),
        "outputDir": str(workspace / "outputs"),
        "documentCount": manifest["documentCount"],
        "copySamples": copy_samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a local REALYTICS pilot workspace outside Git.")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE, help="Pilot workspace path.")
    parser.add_argument("--sample-dir", type=Path, help="Optional source document directory to inventory.")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="Pilot payload template path.")
    parser.add_argument("--copy-samples", action="store_true", help="Copy source files into workspace/documents.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing pilot_input_payload.json.")
    args = parser.parse_args()

    summary = prepare_workspace(
        workspace=args.workspace.resolve(),
        sample_dir=args.sample_dir.resolve() if args.sample_dir else None,
        template=args.template.resolve(),
        copy_samples=args.copy_samples,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
