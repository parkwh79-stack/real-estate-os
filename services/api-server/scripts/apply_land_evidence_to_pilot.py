from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "pilot_input_payload.json"
DEFAULT_EVIDENCE = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "land-evidence-template.json"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from agents.land_ledger_agent import LandLedgerAgent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach verified parcel land evidence to a pilot payload.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Pilot input payload JSON.")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE, help="Filled land evidence JSON.")
    parser.add_argument("--output", type=Path, help="Optional output payload path. Defaults to overwriting --input.")
    args = parser.parse_args()

    payload_path = args.input.resolve()
    output_path = (args.output or args.input).resolve()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))

    updated_payload, summary = apply_evidence(payload, evidence, evidence_file=args.evidence.name)
    output_path.write_text(json.dumps(updated_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**summary, "updated": str(output_path)}, ensure_ascii=False, indent=2))
    return 0


def apply_evidence(payload: Dict[str, Any], evidence: Dict[str, Any], *, evidence_file: str | None = None) -> tuple[Dict[str, Any], Dict[str, Any]]:
    land_ledger = payload.get("landLedger") or payload.get("land_ledger")
    if not isinstance(land_ledger, dict) or not land_ledger:
        raise ValueError("payload.landLedger is required before land evidence can be attached.")

    official_prices = _verified_official_prices(evidence.get("officialLandPrices") or evidence.get("official_land_prices") or [])
    cadastral_records = _verified_cadastral_records(
        evidence.get("cadastralCharacteristics") or evidence.get("cadastral_characteristics") or []
    )
    registry_records = _verified_registry_records(evidence.get("registryEvidence") or evidence.get("registry_evidence") or [])

    enriched = LandLedgerAgent().enrich_analysis(
        land_ledger,
        {
            "mode": "manual_verified_evidence_attachment",
            "officialLandPrices": official_prices,
            "cadastralCharacteristics": cadastral_records,
            "registryEvidence": registry_records,
        },
    )
    payload["landLedger"] = enriched
    payload["landEvidence"] = _land_evidence_summary(
        payload.get("landEvidence") or {},
        enriched,
        official_prices=official_prices,
        cadastral_records=cadastral_records,
        registry_records=registry_records,
        evidence_file=evidence_file,
    )

    payload.setdefault("fixtureMeta", {})
    payload["fixtureMeta"]["landEvidenceUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    notes = payload["fixtureMeta"].setdefault("notes", [])
    note = "Land evidence attachment workflow ran; only non-empty, source-backed evidence records were applied."
    if note not in notes:
        notes.append(note)

    summary = {
        "schemaVersion": "realytics-land-evidence-attachment-summary-v1",
        "officialLandPricesApplied": len(official_prices),
        "cadastralRecordsApplied": len(cadastral_records),
        "registryEvidenceApplied": len(registry_records),
        "landLedgerStatus": enriched.get("validation", {}).get("status"),
        "pfSummaryStatus": enriched.get("pfSummary", {}).get("status"),
        "pnuCoverageRatio": enriched.get("pfSummary", {}).get("pnuCoverageRatio"),
        "officialLandPriceCoverageRatio": enriched.get("pfSummary", {}).get("officialLandPriceCoverageRatio"),
        "registryEvidenceCoverageRatio": enriched.get("pfSummary", {}).get("registryEvidenceCoverageRatio"),
        "reviewMemo": enriched.get("pfSummary", {}).get("reviewMemo"),
    }
    return payload, summary


def _verified_official_prices(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    verified = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if _is_blank(record.get("pnu")) or _is_blank(record.get("pricePerSqm") or record.get("officialLandPricePerSqm")):
            continue
        if _is_blank(record.get("baseYear") or record.get("officialLandPriceBaseYear")):
            continue
        if _is_blank(record.get("sourceName")) or _is_blank(record.get("sourceUrlOrEvidenceId") or record.get("evidenceId")):
            continue
        verified.append(_drop_instruction(record))
    return verified


def _verified_cadastral_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    verified = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if _is_blank(record.get("pnu")):
            continue
        has_evidence = not _is_blank(record.get("sourceName")) and not _is_blank(record.get("sourceUrlOrEvidenceId") or record.get("evidenceId"))
        has_detail = any(not _is_blank(record.get(key)) for key in ["zoning", "standardLandCategoryCode", "baseDate"])
        if has_evidence and has_detail:
            verified.append(_drop_instruction(record))
    return verified


def _verified_registry_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    verified = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if _is_blank(record.get("pnu")):
            continue
        if _is_blank(record.get("evidenceId") or record.get("sourceUrlOrEvidenceId")):
            continue
        if _is_blank(record.get("sourceName")):
            continue
        if _is_blank(record.get("ownerMatchStatus")) or _is_blank(record.get("registeredOwner")):
            continue
        normalized = _drop_instruction(record)
        for key in [
            "mortgageExists",
            "seizureExists",
            "provisionalRegistrationExists",
            "easementExists",
            "trustRegistrationExists",
            "releaseRequired",
        ]:
            normalized[key] = bool(normalized.get(key))
        verified.append(normalized)
    return verified


def _land_evidence_summary(
    existing: Dict[str, Any],
    land_ledger: Dict[str, Any],
    *,
    official_prices: List[Dict[str, Any]],
    cadastral_records: List[Dict[str, Any]],
    registry_records: List[Dict[str, Any]],
    evidence_file: str | None,
) -> Dict[str, Any]:
    summary = land_ledger.get("summary", {})
    pf_summary = land_ledger.get("pfSummary", {})
    metadata = {
        "sourceName": evidence_file or "manual_land_evidence_json",
        "sourceType": "manual_upload",
        "baseDate": datetime.now(timezone.utc).date().isoformat(),
        "confidence": _coverage_average(
            [
                pf_summary.get("pnuCoverageRatio"),
                pf_summary.get("officialLandPriceCoverageRatio"),
                pf_summary.get("registryEvidenceCoverageRatio"),
            ]
        ),
    }
    return {
        **existing,
        "parcelCount": summary.get("parcelCount"),
        "pnuCoverageRatio": pf_summary.get("pnuCoverageRatio"),
        "officialLandPriceCoverageRatio": pf_summary.get("officialLandPriceCoverageRatio"),
        "registryEvidenceCoverageRatio": pf_summary.get("registryEvidenceCoverageRatio"),
        "cadastralRecordCount": len(cadastral_records),
        "officialLandPriceRecordCount": len(official_prices),
        "registryEvidenceRecordCount": len(registry_records),
        "sourceMetadata": metadata,
        "dataLimitations": _data_limitations(pf_summary),
    }


def _data_limitations(pf_summary: Dict[str, Any]) -> List[str]:
    limitations = []
    if (pf_summary.get("officialLandPriceCoverageRatio") or 0) < 1:
        limitations.append("Official land price evidence remains incomplete.")
    if (pf_summary.get("registryEvidenceCoverageRatio") or 0) < 1:
        limitations.append("Registry and encumbrance evidence remains incomplete.")
    if not limitations:
        limitations.append("Land evidence is attached, but legal counsel/human review is still required before final PF approval.")
    return limitations


def _coverage_average(values: List[Any]) -> float:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(numeric) / len(numeric) if numeric else 0.0


def _drop_instruction(record: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in record.items() if key != "fillInstruction"}


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == []


if __name__ == "__main__":
    raise SystemExit(main())
