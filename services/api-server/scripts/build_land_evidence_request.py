from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List


API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "pilot_input_payload.json"
DEFAULT_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "land-evidence-request.json"
DEFAULT_MD_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "land-evidence-request.md"
DEFAULT_TEMPLATE_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "land-evidence-template.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a parcel-level land evidence request list for a pilot run.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Pilot input payload JSON.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON output path.")
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD_OUTPUT, help="Markdown output path.")
    parser.add_argument("--template-output", type=Path, default=DEFAULT_TEMPLATE_OUTPUT, help="Manual evidence JSON template path.")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    evidence_request = build_request(payload)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.md_output.write_text(render_markdown(evidence_request) + "\n", encoding="utf-8")
    args.template_output.write_text(json.dumps(build_manual_template(evidence_request), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "json": str(args.output.resolve()),
                "markdown": str(args.md_output.resolve()),
                "template": str(args.template_output.resolve()),
                "parcelCount": evidence_request["summary"]["parcelCount"],
                "officialLandPriceMissingCount": evidence_request["summary"]["officialLandPriceMissingCount"],
                "registryEvidenceMissingCount": evidence_request["summary"]["registryEvidenceMissingCount"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    land_ledger = payload.get("landLedger") or payload.get("land_ledger") or {}
    parcels = land_ledger.get("parcels") or []
    rows = [_parcel_request_row(parcel) for parcel in parcels if isinstance(parcel, dict)]
    official_missing = [row for row in rows if row["missingEvidence"]["officialLandPrice"]]
    registry_missing = [row for row in rows if row["missingEvidence"]["registryEvidence"]]
    cadastral_missing = [row for row in rows if row["missingEvidence"]["cadastralCharacteristics"]]

    return {
        "schemaVersion": "realytics-land-evidence-request-v1",
        "generatedAt": date.today().isoformat(),
        "projectName": (payload.get("project") or {}).get("name"),
        "purpose": "Parcel-level evidence checklist required before legal review or PF approval readiness.",
        "summary": {
            "parcelCount": len(rows),
            "pnuReadyCount": sum(1 for row in rows if row.get("pnu")),
            "officialLandPriceMissingCount": len(official_missing),
            "registryEvidenceMissingCount": len(registry_missing),
            "cadastralCharacteristicsMissingCount": len(cadastral_missing),
        },
        "requiredEvidencePolicy": [
            "Official land price must include base year, base date, price per sqm, source name, evidence ID or URL, and fetched/issued date.",
            "Registry evidence must include registered owner, ownership share, mortgage/seizure/provisional/easement/trust flags, release requirement, source/evidence ID, and issued/fetched date.",
            "Do not mark land review complete with generated PNU alone.",
            "Do not use registry conclusions without attached evidence or licensed connector metadata.",
        ],
        "parcelEvidenceRequests": rows,
    }


def build_manual_template(request: Dict[str, Any]) -> Dict[str, Any]:
    """Create a fill-in template for parcel-level land evidence."""
    official_prices = []
    cadastral_records = []
    registry_records = []
    for row in request.get("parcelEvidenceRequests", []):
        pnu = row.get("pnu")
        parcel_id = row.get("parcelId")
        address = row.get("addressFragment")
        official_prices.append(
            {
                "parcelId": parcel_id,
                "pnu": pnu,
                "addressFragment": address,
                "pricePerSqm": None,
                "baseYear": None,
                "baseDate": None,
                "sourceName": None,
                "sourceUrlOrEvidenceId": None,
                "fetchedAt": None,
                "confidence": None,
                "fillInstruction": "Fill from official land price evidence. Leave null until verified.",
            }
        )
        cadastral_records.append(
            {
                "parcelId": parcel_id,
                "pnu": pnu,
                "addressFragment": address,
                "landCategory": row.get("landCategory"),
                "standardLandCategoryCode": None,
                "areaSqm": row.get("areaSqm"),
                "zoning": None,
                "landUseZones": [],
                "sourceName": None,
                "sourceUrlOrEvidenceId": None,
                "baseDate": None,
                "confidence": None,
                "fillInstruction": "Fill from cadastral or land-use evidence. Leave null until verified.",
            }
        )
        registry_records.append(
            {
                "parcelId": parcel_id,
                "pnu": pnu,
                "addressFragment": address,
                "evidenceId": None,
                "sourceName": None,
                "sourceUrlOrEvidenceId": None,
                "issuedAt": None,
                "registeredOwner": None,
                "ownerMatchStatus": None,
                "ownershipShare": None,
                "mortgageExists": None,
                "seizureExists": None,
                "provisionalRegistrationExists": None,
                "easementExists": None,
                "trustRegistrationExists": None,
                "restrictionSummary": None,
                "releaseRequired": None,
                "releaseCondition": None,
                "verificationStatus": None,
                "confidence": None,
                "fillInstruction": "Fill from registry/encumbrance evidence. Leave null until verified.",
            }
        )
    return {
        "schemaVersion": "realytics-land-manual-evidence-template-v1",
        "projectName": request.get("projectName"),
        "generatedAt": request.get("generatedAt"),
        "evidencePolicy": request.get("requiredEvidencePolicy", []),
        "officialLandPrices": official_prices,
        "cadastralCharacteristics": cadastral_records,
        "registryEvidence": registry_records,
    }


def _parcel_request_row(parcel: Dict[str, Any]) -> Dict[str, Any]:
    registry = parcel.get("registryEvidence") or {}
    cadastral = parcel.get("cadastralCharacteristics") or {}
    official_price = parcel.get("officialLandPricePerSqm")
    source = parcel.get("source") or {}
    row = source.get("row")
    sheet = source.get("sheet")
    return {
        "parcelId": f"P-{int(parcel.get('sequence') or 0):03d}",
        "sequence": parcel.get("sequence"),
        "pnu": parcel.get("pnu") or parcel.get("resolvedPnu"),
        "addressFragment": parcel.get("addressFragment"),
        "landCategory": parcel.get("landCategory"),
        "areaSqm": parcel.get("areaSqm"),
        "areaPy": parcel.get("areaPy"),
        "totalContractAmount": parcel.get("totalContractAmount"),
        "pnuResolveConfidence": parcel.get("pnuResolveConfidence"),
        "missingEvidence": {
            "officialLandPrice": official_price in (None, ""),
            "registryEvidence": not bool(registry),
            "cadastralCharacteristics": not bool(cadastral),
        },
        "requestedFields": {
            "officialLandPrice": [
                "baseYear",
                "baseDate",
                "pricePerSqm",
                "officialTotalLandValue",
                "sourceName",
                "sourceUrlOrEvidenceId",
                "fetchedAt",
            ],
            "registryEvidence": [
                "evidenceId",
                "issuedAt",
                "registeredOwner",
                "ownerMatchStatus",
                "ownershipShare",
                "mortgageExists",
                "seizureExists",
                "provisionalRegistrationExists",
                "easementExists",
                "trustRegistrationExists",
                "restrictionSummary",
                "releaseRequired",
                "releaseCondition",
            ],
            "cadastralCharacteristics": [
                "landCategory",
                "standardLandCategoryCode",
                "areaSqm",
                "zoning",
                "landUseZones",
                "sourceName",
                "sourceUrlOrEvidenceId",
                "baseDate",
            ],
        },
        "sourceLocators": [
            {
                "sourceType": "excel_row",
                "fileName": "01.본리동토지조서.xls",
                "sheetName": sheet,
                "row": row,
                "fieldName": "landLedger.parcel",
            }
        ]
        if row
        else [],
    }


def render_markdown(request: Dict[str, Any]) -> str:
    lines = [
        "# Land Evidence Request",
        "",
        f"Generated: {request['generatedAt']}",
        f"Project: {request.get('projectName') or ''}",
        "",
        "## Summary",
        "",
        f"- Parcel count: {request['summary']['parcelCount']}",
        f"- PNU ready count: {request['summary']['pnuReadyCount']}",
        f"- Official land price missing: {request['summary']['officialLandPriceMissingCount']}",
        f"- Registry evidence missing: {request['summary']['registryEvidenceMissingCount']}",
        f"- Cadastral characteristics missing: {request['summary']['cadastralCharacteristicsMissingCount']}",
        "",
        "## Parcel Requests",
        "",
        "| Parcel | PNU | Address | Area sqm | Contract amount | Missing evidence |",
        "|---|---|---|---:|---:|---|",
    ]
    for row in request["parcelEvidenceRequests"]:
        missing = [
            label
            for label, missing_value in row["missingEvidence"].items()
            if missing_value
        ]
        lines.append(
            "| {parcel} | {pnu} | {address} | {area} | {amount} | {missing} |".format(
                parcel=row["parcelId"],
                pnu=row.get("pnu") or "",
                address=row.get("addressFragment") or "",
                area=_fmt_number(row.get("areaSqm")),
                amount=_fmt_number(row.get("totalContractAmount")),
                missing=", ".join(missing) if missing else "none",
            )
        )
    lines.extend(
        [
            "",
            "## Policy",
            "",
        ]
    )
    for item in request["requiredEvidencePolicy"]:
        lines.append(f"- {item}")
    return "\n".join(lines)


def _fmt_number(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:,.0f}"
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
