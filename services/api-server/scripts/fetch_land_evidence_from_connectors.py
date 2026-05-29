from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "pilot_input_payload.json"
DEFAULT_CONNECTOR_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "land-evidence-connector-result.json"
DEFAULT_EVIDENCE_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "land-evidence-from-connectors.json"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from integrations.land_data_connectors import LandDataConnectorRouter  # noqa: E402
from scripts.apply_land_evidence_to_pilot import apply_evidence  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch parcel-level cadastral and official land price evidence through configured connectors."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Pilot input payload JSON.")
    parser.add_argument("--connector-output", type=Path, default=DEFAULT_CONNECTOR_OUTPUT, help="Raw connector result JSON path.")
    parser.add_argument("--evidence-output", type=Path, default=DEFAULT_EVIDENCE_OUTPUT, help="Evidence JSON path usable by apply_land_evidence_to_pilot.py.")
    parser.add_argument("--legal-dong-code", help="Legal dong code for PNU generation. Defaults to payload.land.legalDongCode.")
    parser.add_argument("--base-year", default="2026", help="Official land price base year to request.")
    parser.add_argument("--timeout-seconds", type=float, default=8.0, help="Per-request connector timeout.")
    parser.add_argument("--apply", action="store_true", help="Apply fetched non-empty evidence records back to the input payload.")
    parser.add_argument("--output", type=Path, help="When --apply is used, optional output payload path. Defaults to overwriting --input.")
    args = parser.parse_args()

    payload_path = args.input.resolve()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    connector_result = fetch_connector_evidence(
        payload,
        legal_dong_code=args.legal_dong_code,
        base_year=args.base_year,
        timeout_seconds=args.timeout_seconds,
    )
    evidence_payload = build_evidence_payload(payload, connector_result)

    args.connector_output.parent.mkdir(parents=True, exist_ok=True)
    args.connector_output.write_text(json.dumps(connector_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.evidence_output.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    applied_summary: Dict[str, Any] | None = None
    if args.apply:
        output_path = (args.output or args.input).resolve()
        updated_payload, applied_summary = apply_evidence(
            payload,
            evidence_payload,
            evidence_file=args.evidence_output.name,
        )
        output_path.write_text(json.dumps(updated_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "schemaVersion": "realytics-land-connector-fetch-summary-v1",
        "connectorOutput": str(args.connector_output.resolve()),
        "evidenceOutput": str(args.evidence_output.resolve()),
        "pnuResolutionCount": len(connector_result.get("pnuResolutions") or []),
        "cadastralRecordCount": len(connector_result.get("cadastralCharacteristics") or []),
        "officialLandPriceRecordCount": len(connector_result.get("officialLandPrices") or []),
        "registryEvidenceRecordCount": len(connector_result.get("registryEvidence") or []),
        "diagnosticCount": len(connector_result.get("diagnostics") or []),
        "diagnosticStatuses": sorted({item.get("status") for item in connector_result.get("diagnostics", []) if item.get("status")}),
        "appliedSummary": applied_summary,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def fetch_connector_evidence(
    payload: Dict[str, Any],
    *,
    legal_dong_code: str | None,
    base_year: str | int | None,
    timeout_seconds: float,
) -> Dict[str, Any]:
    land_ledger = payload.get("landLedger") or payload.get("land_ledger") or {}
    parcels = land_ledger.get("parcels") or []
    if not parcels:
        raise ValueError("payload.landLedger.parcels is required before connector evidence fetch.")
    resolved_legal_dong_code = (
        legal_dong_code
        or (payload.get("land") or {}).get("legalDongCode")
        or (payload.get("land") or {}).get("legal_dong_code")
    )
    return LandDataConnectorRouter().fetch_records_for_parcels(
        parcels,
        legal_dong_code=resolved_legal_dong_code,
        base_year=base_year,
        timeout_seconds=timeout_seconds,
    )


def build_evidence_payload(payload: Dict[str, Any], connector_result: Dict[str, Any]) -> Dict[str, Any]:
    project = payload.get("project") or {}
    return {
        "schemaVersion": "realytics-land-connector-evidence-v1",
        "projectName": project.get("name"),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceType": "configured_public_api_connector",
        "sourceMetadata": {
            "sourceName": "LandDataConnectorRouter",
            "sourceType": "public_api_or_configured_connector",
            "sourceUrlOrEvidenceId": "land-evidence-connector-result.json",
            "confidence": _connector_confidence(connector_result),
        },
        "pnuResolutions": connector_result.get("pnuResolutions") or [],
        "officialLandPrices": connector_result.get("officialLandPrices") or [],
        "cadastralCharacteristics": connector_result.get("cadastralCharacteristics") or [],
        "registryEvidence": connector_result.get("registryEvidence") or [],
        "diagnostics": connector_result.get("diagnostics") or [],
        "dataSourcePolicy": connector_result.get("dataSourcePolicy") or {},
    }


def _connector_confidence(connector_result: Dict[str, Any]) -> float:
    diagnostics = connector_result.get("diagnostics") or []
    fetched = sum(1 for item in diagnostics if item.get("status") == "fetched")
    failed = sum(1 for item in diagnostics if item.get("status") not in {"fetched"})
    if fetched == 0:
        return 0.0
    return max(0.0, min(1.0, fetched / max(fetched + failed, 1)))


if __name__ == "__main__":
    raise SystemExit(main())
