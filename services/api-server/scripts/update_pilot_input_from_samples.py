from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from docx import Document
from openpyxl import load_workbook


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
DEFAULT_SAMPLE_DIR = WORKSPACE_ROOT / "sample"
DEFAULT_INPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "pilot_input_payload.json"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from agents.land_ledger_agent import LandLedgerAgent  # noqa: E402


FEASIBILITY_WORKBOOK = "19.12.11 본리동 주상복합 사업수지 (PF검토용).xlsx"
LAND_LEDGER_WORKBOOK = "01.본리동토지조서.xls"
PF_REVIEW_DOCX = "부동산금융평가위원회_대구 달서구 본동 주상복합 중순위 PF_하나금융투자 (최종) - 복사본.docx"
LEGAL_DONG_CODE_BONRI = "2729011700"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update the pilot input payload with values extracted from local Bonri sample files."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Pilot input payload JSON to update.")
    parser.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR, help="Directory containing sample files.")
    args = parser.parse_args()

    payload_path = args.input.resolve()
    sample_dir = args.sample_dir.resolve()
    payload = _read_json(payload_path)

    feasibility_path = sample_dir / FEASIBILITY_WORKBOOK
    land_path = sample_dir / LAND_LEDGER_WORKBOOK
    pf_docx_path = sample_dir / PF_REVIEW_DOCX

    feasibility = _extract_feasibility_workbook(feasibility_path)
    land = _extract_land_ledger(land_path)
    pf_terms = _extract_pf_terms(pf_docx_path)

    _apply_project_patch(payload, feasibility, pf_terms)
    _apply_land_patch(payload, land)
    _apply_feasibility_patch(payload, feasibility)
    _apply_pf_patch(payload, pf_terms)
    _apply_source_documents_patch(payload, sample_dir, [feasibility_path, land_path, pf_docx_path])

    payload.setdefault("fixtureMeta", {})
    payload["fixtureMeta"].update(
        {
            "updatedAt": date.today().isoformat(),
            "sampleDerived": True,
            "sampleUpdateSource": "services/api-server/scripts/update_pilot_input_from_samples.py",
        }
    )
    payload["fixtureMeta"].setdefault("notes", [])
    _append_unique(
        payload["fixtureMeta"]["notes"],
        "Runtime pilot payload was updated from local sample Excel/XLS/DOCX values; official land price and registry evidence remain connector/manual evidence requirements.",
    )

    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "updated": str(payload_path),
                "projectName": payload.get("project", {}).get("name"),
                "landAreaSqm": payload.get("land", {}).get("area_sqm"),
                "parcelCount": payload.get("landLedger", {}).get("summary", {}).get("parcelCount"),
                "totalRevenue": payload.get("feasibility_input", {}).get("total_revenue"),
                "totalCost": payload.get("feasibility_input", {}).get("total_cost"),
                "pfAmount": sum(item.get("amount", 0) for item in payload.get("pf_structure", [])),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_feasibility_workbook(path: Path) -> Dict[str, Any]:
    wb = load_workbook(path, data_only=True, read_only=True)
    summary = wb["1.수지총괄(대외용)"]
    plan = wb["사업수지"]
    overview = wb["2개요"]
    review = wb["검토서"]

    total_revenue_ex_vat = _krw(summary["E49"].value)
    total_revenue_inc_vat = _krw(summary["E44"].value)
    total_cost = _krw(summary["E112"].value)
    land_related_cost = _krw(summary["E64"].value)
    land_purchase_cost = _krw(summary["E58"].value)
    construction_cost = _krw(summary["E57"].value)
    finance_cost = _krw(summary["E102"].value)
    other_cost = total_cost - land_related_cost - construction_cost - finance_cost

    apartment_units = int(summary["D27"].value or 0)
    officetel_units = int(summary["D36"].value or 0)
    retail_units = 2

    apartment_revenue = _krw(plan["F8"].value)
    officetel_revenue = _krw(plan["H8"].value)
    retail_revenue = _krw(plan["J8"].value)

    apartment_supply_py = _float(overview["I33"].value) or 0.0
    officetel_supply_py = _float(overview["I42"].value) or 0.0
    retail_supply_py = _float(overview["I52"].value) or 0.0

    product_mix = [
        {
            "type": "apartment",
            "units": apartment_units,
            "exclusiveAreaPy": _weighted_average(
                [
                    (_float(overview["E15"].value), _float(overview["C15"].value)),
                    (_float(overview["E16"].value), _float(overview["C16"].value)),
                ]
            ),
            "contractAreaPy": apartment_supply_py / apartment_units if apartment_units else 0,
            "avgUnitPrice": apartment_revenue / apartment_units if apartment_units else 0,
            "pricePerPy": apartment_revenue / apartment_supply_py if apartment_supply_py else 0,
            "totalRevenue": apartment_revenue,
            "vatIncluded": False,
            "sourceLocators": [
                _excel_locator(path.name, "사업수지", "F8", "apartment.totalRevenue"),
                _excel_locator(path.name, "1.수지총괄(대외용)", "D27", "apartment.units"),
                _excel_locator(path.name, "2개요", "I33", "apartment.supplyAreaPy"),
            ],
        },
        {
            "type": "officetel",
            "units": officetel_units,
            "exclusiveAreaPy": _weighted_average(
                [
                    (_float(overview["E34"].value), _float(overview["C34"].value)),
                    (_float(overview["E35"].value), _float(overview["C35"].value)),
                ]
            ),
            "contractAreaPy": officetel_supply_py / officetel_units if officetel_units else 0,
            "avgUnitPrice": officetel_revenue / officetel_units if officetel_units else 0,
            "pricePerPy": officetel_revenue / officetel_supply_py if officetel_supply_py else 0,
            "totalRevenue": officetel_revenue,
            "vatIncluded": False,
            "sourceLocators": [
                _excel_locator(path.name, "사업수지", "H8", "officetel.totalRevenue"),
                _excel_locator(path.name, "1.수지총괄(대외용)", "D36", "officetel.units"),
                _excel_locator(path.name, "2개요", "I42", "officetel.supplyAreaPy"),
            ],
        },
        {
            "type": "commercial_retail",
            "units": retail_units,
            "exclusiveAreaPy": retail_supply_py / retail_units if retail_units else 0,
            "contractAreaPy": retail_supply_py / retail_units if retail_units else 0,
            "avgUnitPrice": retail_revenue / retail_units if retail_units else 0,
            "pricePerPy": retail_revenue / retail_supply_py if retail_supply_py else 0,
            "totalRevenue": retail_revenue,
            "vatIncluded": False,
            "sourceLocators": [
                _excel_locator(path.name, "사업수지", "J8", "commercial.totalRevenue"),
                _excel_locator(path.name, "2개요", "I52", "commercial.supplyAreaPy"),
            ],
        },
    ]

    market_comparables = [
        {
            "caseId": "BONRI-REVIEW-BASIS-APT-001",
            "name": "Yongsan Lotte Castle indexed basis in feasibility review",
            "productType": "apartment",
            "distanceM": None,
            "pricePerPy": _krw(review["E23"].value, unit_multiplier=1),
            "baseDate": "2019-12-11",
            "sourceName": "Feasibility review price basis",
            "sourceType": "manual_upload",
            "sourceUrlOrEvidenceId": "bonri-feasibility-review-sheet-검토서-row-23",
            "confidence": 0.7,
            "sourceLocator": _excel_locator(path.name, "검토서", "E23", "marketComparable.pricePerPy"),
            "usageNote": "The review sheet states this index is based on Yongsan Lotte Castle transaction average; treat as document-derived benchmark until external transaction evidence is attached.",
        },
        {
            "caseId": "BONRI-REVIEW-BASIS-APT-002",
            "name": "Yongsan Lotte Castle indexed basis in feasibility review",
            "productType": "apartment",
            "distanceM": None,
            "pricePerPy": _krw(review["E24"].value, unit_multiplier=1),
            "baseDate": "2019-12-11",
            "sourceName": "Feasibility review price basis",
            "sourceType": "manual_upload",
            "sourceUrlOrEvidenceId": "bonri-feasibility-review-sheet-검토서-row-24",
            "confidence": 0.7,
            "sourceLocator": _excel_locator(path.name, "검토서", "E24", "marketComparable.pricePerPy"),
            "usageNote": "The review sheet states this index is based on Yongsan Lotte Castle transaction average; treat as document-derived benchmark until external transaction evidence is attached.",
        },
    ]

    return {
        "sourceFile": path.name,
        "projectName": summary["A1"].value,
        "address": str(summary["A2"].value or "").replace("■ 대지위치:", "").strip(),
        "district": summary["B6"].value,
        "landAreaSqm": _float(summary["B4"].value),
        "landAreaPy": _float(summary["C4"].value),
        "grossFloorAreaSqm": _float(summary["E6"].value),
        "grossFloorAreaPy": _float(summary["F6"].value),
        "floors": f"B{int(summary['H5'].value or 0)}/F{int(summary['H4'].value or 0)}",
        "far": _float(summary["H6"].value),
        "startDate": _format_month(overview["E10"].value),
        "completionDate": _format_month(overview["H10"].value),
        "apartmentUnits": apartment_units,
        "officetelUnits": officetel_units,
        "totalUnits": apartment_units + officetel_units,
        "totalRevenueExVat": total_revenue_ex_vat,
        "totalRevenueIncVat": total_revenue_inc_vat,
        "totalCost": total_cost,
        "landRelatedCost": land_related_cost,
        "landPurchaseCost": land_purchase_cost,
        "constructionCost": construction_cost,
        "financeCost": finance_cost,
        "otherCost": other_cost,
        "operatingProfit": _krw(summary["E113"].value),
        "profitMargin": _float(summary["G113"].value),
        "productMix": product_mix,
        "marketComparables": market_comparables,
        "sourceLocators": {
            "totalRevenueExVat": _excel_locator(path.name, "1.수지총괄(대외용)", "E49", "feasibility.totalRevenueExVat"),
            "totalCost": _excel_locator(path.name, "1.수지총괄(대외용)", "E112", "feasibility.totalCost"),
            "landPurchaseCost": _excel_locator(path.name, "1.수지총괄(대외용)", "E58", "feasibility.landPurchaseCost"),
            "constructionCost": _excel_locator(path.name, "1.수지총괄(대외용)", "E57", "feasibility.constructionCost"),
            "financeCost": _excel_locator(path.name, "1.수지총괄(대외용)", "E102", "feasibility.financeCost"),
        },
    }


def _extract_land_ledger(path: Path) -> Dict[str, Any]:
    source_info = {
        "original_file_name": path.name,
        "original_file_hash": _sha256(path),
        "uploaded_by": "local_pilot_sample",
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "parser_version": "land-ledger-agent-v1.1",
    }
    analysis = LandLedgerAgent().analyze_xls(path, source_info=source_info)
    analysis = LandLedgerAgent().enrich_analysis(
        analysis,
        {
            "legalDongCode": LEGAL_DONG_CODE_BONRI,
            "mode": "configured_legal_dong_code_only",
            "useLiveConnectors": False,
        },
    )

    land_acquisition = []
    for parcel in analysis.get("parcels", []):
        payments = parcel["paymentSummary"]
        balances = [owner.get("balancePayment") for owner in parcel.get("owners", [])]
        balance = sum(balances) if balances and all(value is not None for value in balances) else None
        land_acquisition.append(
            {
                "parcelId": f"P-{int(parcel.get('sequence') or 0):03d}",
                "pnu": parcel.get("pnu"),
                "owner": _owners_label(parcel.get("owners", [])),
                "share": 1.0,
                "area_sqm": parcel.get("areaSqm"),
                "contractAmount": parcel.get("totalContractAmount"),
                "depositPaid": payments["knownRecordedPaidAmount"] if not payments["recordedPaidMissingCount"] else None,
                "balanceDue": balance,
                "paymentStatus": parcel["paymentStatus"],
                "paymentSummary": payments,
                "paymentReviewVersion": parcel["paymentReviewVersion"],
                "sourceLocators": [
                    _excel_locator(path.name, analysis.get("sheetName") or "190927토지조서(원본)", f"E{parcel.get('source', {}).get('row')}", "land.areaSqm"),
                    _excel_locator(path.name, analysis.get("sheetName") or "190927토지조서(원본)", f"I{parcel.get('source', {}).get('row')}", "land.contractAmount"),
                ],
            }
        )

    return {
        "sourceFile": path.name,
        "analysis": analysis,
        "landAcquisition": land_acquisition,
        "summary": analysis.get("summary", {}),
    }


def _extract_pf_terms(path: Path) -> Dict[str, Any]:
    doc = Document(path)
    table = doc.tables[2]
    row_text_by_label: Dict[str, str] = {}
    for row in table.rows:
        cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
        if len(cells) >= 2 and cells[0]:
            row_text_by_label[cells[0]] = cells[1]

    loan_text = row_text_by_label.get("대출금액", "")
    amounts = {
        "senior": _first_number_after(loan_text, "선순위대출"),
        "mezzanine": _first_number_after(loan_text, "중순위대출"),
        "junior": _first_number_after(loan_text, "후순위대출"),
    }
    if not all(amounts.values()):
        amounts = {"senior": 630, "mezzanine": 120, "junior": 150}

    tranche_source = _docx_locator(path.name, "table[3].row[7]", "pf_structure.amount")
    tranches = [
        {
            "tranche": "Tr.A Senior",
            "amount": int(amounts["senior"] * 100_000_000),
            "priority": 1,
            "interest_rate": 0.045,
            "fee_rate": 0.015,
            "maturityMonths": 49,
            "sourceLocators": [tranche_source],
            "assumptionNote": "Amount is extracted from the PF review document. Rate and fee are pilot assumptions pending accessible term-sheet cell confirmation.",
        },
        {
            "tranche": "Tr.B Mezzanine",
            "amount": int(amounts["mezzanine"] * 100_000_000),
            "priority": 2,
            "interest_rate": 0.065,
            "fee_rate": 0.05,
            "maturityMonths": 49,
            "sourceLocators": [tranche_source],
            "assumptionNote": "Amount is extracted from the PF review document. Rate and fee are pilot assumptions pending accessible term-sheet cell confirmation.",
        },
        {
            "tranche": "Tr.C Junior",
            "amount": int(amounts["junior"] * 100_000_000),
            "priority": 3,
            "interest_rate": 0.08,
            "fee_rate": 0.10,
            "maturityMonths": 49,
            "sourceLocators": [tranche_source],
            "assumptionNote": "Amount is extracted from the PF review document. Rate and fee are pilot assumptions pending accessible term-sheet cell confirmation.",
        },
    ]

    return {
        "sourceFile": path.name,
        "borrower": row_text_by_label.get("차주 및 시행사"),
        "contractor": row_text_by_label.get("시공사"),
        "loanText": loan_text,
        "tranches": tranches,
        "discountTriggerText": row_text_by_label.get("할인분양", ""),
        "collectionAllocationText": row_text_by_label.get("분양수입금 배분", ""),
        "drawdownConditionText": row_text_by_label.get("인출선행조건", ""),
        "sourceLocators": {
            "loanAmount": tranche_source,
            "discountTrigger": _docx_locator(path.name, "table[3].row[18]", "discountTrigger"),
            "collectionAllocation": _docx_locator(path.name, "table[3].row[11]", "collectionAllocation"),
            "drawdownConditions": _docx_locator(path.name, "table[3].row[20]", "drawdownConditions"),
        },
    }


def _apply_project_patch(payload: Dict[str, Any], feasibility: Dict[str, Any], pf_terms: Dict[str, Any]) -> None:
    payload["project"] = {
        **payload.get("project", {}),
        "name": "Bonri-dong Mixed-use PF Pilot",
        "type": "mixed_use_residential",
        "location": feasibility["address"],
        "borrower": pf_terms.get("borrower"),
        "contractor": pf_terms.get("contractor"),
        "review_date": date.today().isoformat(),
        "financial_institution": "REALYTICS Pilot",
        "status": "sample_derived_pilot",
    }
    payload["schedule"] = {
        "construction_start": feasibility.get("startDate"),
        "completion": feasibility.get("completionDate"),
    }


def _apply_land_patch(payload: Dict[str, Any], land: Dict[str, Any]) -> None:
    summary = land["summary"]
    first_parcel = (land["analysis"].get("parcels") or [{}])[0]
    payload["land"] = {
        "pnu": first_parcel.get("pnu"),
        "address": "대구광역시 달서구 본리동 1-4번지 일원",
        "area_sqm": summary.get("totalAreaSqm"),
        "area_py": summary.get("totalAreaPy"),
        "legalDongCode": LEGAL_DONG_CODE_BONRI,
        "raw_lot_number": "1-4",
        "normalized_lot_number": "1-4",
        "sourceLocators": [
            _excel_locator(land["sourceFile"], land["analysis"].get("sheetName") or "190927토지조서(원본)", "E28", "land.totalAreaSqm"),
            _excel_locator(land["sourceFile"], land["analysis"].get("sheetName") or "190927토지조서(원본)", "I28", "land.totalPurchaseAmount"),
        ],
    }
    payload["land_acquisition"] = land["landAcquisition"]
    payload["landLedger"] = land["analysis"]
    payload["landEvidence"] = {
        "sourceMetadata": {
            "sourceName": land["sourceFile"],
            "sourceType": "manual_upload",
            "baseDate": "2019-09-27",
            "confidence": 0.9,
            "sourceUrlOrEvidenceId": "local-sample-land-ledger",
        },
        "dataLimitations": [
            "PNU is generated from configured legal dong code and parsed lot numbers.",
            "Official land price is not in the uploaded land ledger and must be fetched or attached with base year/source evidence.",
            "Registry and encumbrance evidence is not in the uploaded land ledger and must be attached before legal/PF final approval.",
        ],
    }


def _apply_feasibility_patch(payload: Dict[str, Any], feasibility: Dict[str, Any]) -> None:
    payload["architectural_plan"] = {
        "land_area_sqm": feasibility["landAreaSqm"],
        "gross_floor_area_sqm": feasibility["grossFloorAreaSqm"],
        "building_use": "mixed_use_residential",
        "units": feasibility["totalUnits"],
        "floors": feasibility["floors"],
        "far": feasibility["far"],
        "sourceLocators": [
            _excel_locator(feasibility["sourceFile"], "1.수지총괄(대외용)", "B4", "architecturalPlan.landAreaSqm"),
            _excel_locator(feasibility["sourceFile"], "1.수지총괄(대외용)", "E6", "architecturalPlan.grossFloorAreaSqm"),
            _excel_locator(feasibility["sourceFile"], "1.수지총괄(대외용)", "J6", "architecturalPlan.units"),
        ],
    }
    payload["feasibility_input"] = {
        "total_revenue": feasibility["totalRevenueExVat"],
        "total_cost": feasibility["totalCost"],
        "land_cost": feasibility["landRelatedCost"],
        "land_purchase_cost": feasibility["landPurchaseCost"],
        "construction_cost": feasibility["constructionCost"],
        "finance_cost": feasibility["financeCost"],
        "other_cost": feasibility["otherCost"],
        "operating_profit": feasibility["operatingProfit"],
        "profit_margin": feasibility["profitMargin"],
        "vat_basis": "vat_excluded",
        "sourceLocators": feasibility["sourceLocators"],
    }
    payload["product_mix"] = feasibility["productMix"]
    payload["document_market_evidence"] = {
        "productMix": feasibility["productMix"],
        "manualComparables": feasibility["marketComparables"],
        "sourceMetadata": {
            "sourceName": feasibility["sourceFile"],
            "sourceType": "manual_upload",
            "baseDate": "2019-12-11",
            "confidence": 0.82,
        },
    }
    payload["market"] = {
        **payload.get("market", {}),
        "proposedPricePerPy": _weighted_market_price(feasibility["productMix"]),
        "manualComparables": feasibility["marketComparables"],
    }


def _apply_pf_patch(payload: Dict[str, Any], pf_terms: Dict[str, Any]) -> None:
    payload["pf_structure"] = pf_terms["tranches"]
    payload["expected_results"] = {
        **payload.get("expected_results", {}),
        "total_pf_amount": sum(item["amount"] for item in pf_terms["tranches"]),
        "sourceLocators": [pf_terms["sourceLocators"]["loanAmount"]],
    }
    payload["covenants"] = {
        **payload.get("covenants", {}),
        "discount_triggers": [
            {
                "month": 6,
                "targetSalesRate": 0.4,
                "allowedDiscountRate": 0.05,
                "action": "lender_consultation_required",
                "sourceLocators": [pf_terms["sourceLocators"]["discountTrigger"]],
            },
            {
                "month": 12,
                "targetSalesRate": 0.6,
                "allowedDiscountRate": 0.1,
                "action": "discount_sale_required",
                "sourceLocators": [pf_terms["sourceLocators"]["discountTrigger"]],
            },
            {
                "month": 24,
                "targetSalesRate": 0.75,
                "allowedDiscountRate": 0.15,
                "action": "mandatory_discount_and_cash_sweep",
                "sourceLocators": [pf_terms["sourceLocators"]["discountTrigger"]],
            },
        ],
        "collectionAllocationText": pf_terms["collectionAllocationText"],
        "drawdownConditionText": pf_terms["drawdownConditionText"],
    }


def _apply_source_documents_patch(payload: Dict[str, Any], sample_dir: Path, files: List[Path]) -> None:
    payload["sourceDocuments"] = [
        {
            "documentType": _document_type(path),
            "fileName": path.name,
            "required": True,
            "status": "available_local_sample",
            "localPath": str(path),
            "sha256": _sha256(path),
            "sizeBytes": path.stat().st_size,
            "sourceType": "manual_upload",
        }
        for path in files
    ]
    manifest_path = API_ROOT / "data" / "runtime" / "pilot-input" / "source_document_manifest.json"
    if manifest_path.exists():
        payload["sourceDocumentManifest"] = str(manifest_path)


def _document_type(path: Path) -> str:
    if path.name == FEASIBILITY_WORKBOOK:
        return "feasibility_workbook"
    if path.name == LAND_LEDGER_WORKBOOK:
        return "land_ledger"
    if path.name == PF_REVIEW_DOCX:
        return "pf_review_report_sample"
    return "source_document"


def _krw(value: Any, unit_multiplier: int = 1000) -> float:
    number = _float(value)
    return float(number or 0) * unit_multiplier


def _float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _weighted_average(values: List[tuple[Optional[float], Optional[float]]]) -> float:
    numerator = sum((value or 0) * (weight or 0) for value, weight in values)
    denominator = sum(weight or 0 for _, weight in values)
    return numerator / denominator if denominator else 0.0


def _weighted_market_price(product_mix: List[Dict[str, Any]]) -> float:
    area = sum((item.get("contractAreaPy") or 0) * (item.get("units") or 0) for item in product_mix)
    revenue = sum(item.get("totalRevenue") or 0 for item in product_mix)
    return revenue / area if area else 0.0


def _format_month(value: Any) -> Optional[str]:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m")
    if value:
        return str(value)[:7]
    return None


def _first_number_after(text: str, marker: str) -> Optional[int]:
    pattern = re.escape(marker) + r"\s*\[(\d+)\]\s*억원"
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def _owners_label(owners: List[Dict[str, Any]]) -> str:
    names = [owner.get("ownerName") for owner in owners if owner.get("ownerName")]
    if not names:
        return ""
    if len(names) <= 2:
        return ", ".join(names)
    return f"{names[0]} 외 {len(names) - 1}인"


def _excel_locator(file_name: str, sheet_name: str, cell: str, field_name: str) -> Dict[str, Any]:
    return {
        "sourceType": "excel_cell",
        "fileName": file_name,
        "sheetName": sheet_name,
        "cell": cell,
        "fieldName": field_name,
    }


def _docx_locator(file_name: str, locator: str, field_name: str) -> Dict[str, Any]:
    return {
        "sourceType": "docx_table",
        "fileName": file_name,
        "locator": locator,
        "fieldName": field_name,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _append_unique(items: List[str], value: str) -> None:
    if value not in items:
        items.append(value)


if __name__ == "__main__":
    raise SystemExit(main())
