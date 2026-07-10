"""Run a real parcel-ledger pilot without fabricating external evidence.

The script parses a supplied XLS/XLSX ledger, performs deterministic PNU
resolution when a confirmed legal-dong code is supplied, and writes a review
artifact. Official land price and registry evidence are only attached when
the connector/manual evidence files are provided explicitly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from agents.land_ledger_agent import LandLedgerAgent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--legal-dong-code", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--evidence", type=Path, help="Optional source-backed evidence JSON.")
    args = parser.parse_args()

    agent = LandLedgerAgent()
    analysis = agent.analyze_xls(
        args.workbook,
        source_info={
            "original_file_name": args.workbook.name,
            "uploaded_by": "pilot_e2e",
            "project_id": args.output.stem,
        },
    )
    options = {
        "legalDongCode": args.legal_dong_code,
        "mode": "deterministic_pnu_plus_supplied_evidence",
        "useLiveConnectors": False,
    }
    if args.evidence:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        options.update(evidence)
    enriched = agent.enrich_analysis(analysis, options)
    enriched["pilotExecution"] = {
        "workbook": str(args.workbook),
        "parcelCount": len(enriched.get("parcels") or []),
        "pnuResolvedCount": sum(bool(item.get("pnu") or item.get("resolvedPnu")) for item in enriched.get("parcels") or []),
        "officialLandPriceCount": sum(bool(item.get("officialLandPricePerSqm")) for item in enriched.get("parcels") or []),
        "registryEvidenceCount": sum(bool(item.get("registryEvidence")) for item in enriched.get("parcels") or []),
        "externalEvidenceFabricated": False,
        "nextActions": [
            "Attach base-year official land price evidence for each parcel.",
            "Attach issued registry evidence and encumbrance flags for each parcel.",
            "Re-run Land-Legal Pod and Approval Readiness Gate after evidence review.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(enriched, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(enriched["pilotExecution"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
