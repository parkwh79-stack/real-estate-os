from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW_TEMPLATE = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "pilot-review-actions-template.json"
DEFAULT_PACKAGE_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "pilot-legal-evidence-package-template.json"
DEFAULT_ACTIONS_OUTPUT = API_ROOT / "data" / "runtime" / "pilot-input" / "outputs" / "pilot-review-actions-from-legal-evidence.json"


EVIDENCE_REQUIREMENTS = {
    "PERMIT_SCHEDULE_READINESS": {
        "riskTitle": "Permit schedule readiness",
        "evidenceTypes": [
            "permit_schedule",
            "permit_application_receipt",
            "approval_expected_date_evidence",
            "delay_buffer_analysis",
        ],
        "minimumSatisfiedCount": 4,
        "resolvedComment": "Permit schedule, application/approval evidence, and delay buffer analysis are attached.",
    },
    "LEGAL_LAND_RIGHTS": {
        "riskTitle": "Land rights and encumbrances",
        "evidenceTypes": [
            "registry_record",
            "encumbrance_release_condition",
            "ownership_transfer_evidence",
            "trust_registration_evidence",
        ],
        "minimumSatisfiedCount": 4,
        "resolvedComment": "Registry, encumbrance release, ownership transfer, and trust-registration evidence are attached.",
    },
    "COVENANT_AND_LEGAL_PACKAGE_COMPLETENESS": {
        "riskTitle": "Covenant and legal package completeness",
        "evidenceTypes": [
            "loan_agreement_draft",
            "trust_agreement",
            "responsible_completion_letter",
            "covenant_eod_checklist",
            "legal_opinion",
        ],
        "minimumSatisfiedCount": 5,
        "resolvedComment": "Loan, trust, responsible completion, covenant/EOD, and legal opinion evidence are attached.",
    },
}


LEGAL_HANDOFF_TYPES = {
    "legal_to_credit_review",
    "risk_register_to_credit_review",
    "credit_review_to_report",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a legal/permit/covenant evidence package template or convert a filled package into review actions."
    )
    parser.add_argument("--review-template", type=Path, default=DEFAULT_REVIEW_TEMPLATE, help="Pilot review action template JSON.")
    parser.add_argument("--output", type=Path, default=DEFAULT_PACKAGE_OUTPUT, help="Evidence package template JSON output.")
    parser.add_argument("--legal-evidence-package", type=Path, help="Filled legal evidence package JSON.")
    parser.add_argument("--review-actions-output", type=Path, default=DEFAULT_ACTIONS_OUTPUT, help="Review actions JSON output.")
    args = parser.parse_args()

    review_template = _load(args.review_template)
    if not args.legal_evidence_package:
        package = build_legal_evidence_package_template(review_template)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "schemaVersion": "realytics-legal-evidence-package-template-output-v1",
            "template": str(args.output.resolve()),
            "requiredEvidenceCount": len(package["documents"]),
            "riskRequirementCount": len(package["riskRequirements"]),
        }, ensure_ascii=False, indent=2))
        return 0

    evidence_package = _load(args.legal_evidence_package)
    actions = build_review_actions_from_legal_evidence_package(review_template, evidence_package)
    args.review_actions_output.parent.mkdir(parents=True, exist_ok=True)
    args.review_actions_output.write_text(json.dumps(actions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schemaVersion": "realytics-legal-evidence-review-action-output-v1",
        "reviewActions": str(args.review_actions_output.resolve()),
        "resolvedRiskCount": sum(1 for item in actions.get("riskReviews", []) if item.get("reviewStatus") == "resolved"),
        "needsRevisionRiskCount": sum(1 for item in actions.get("riskReviews", []) if item.get("reviewStatus") == "needs_revision"),
        "approvedHandoffCount": sum(1 for item in actions.get("handoffReviews", []) if item.get("reviewStatus") == "approved"),
        "needsRevisionHandoffCount": sum(1 for item in actions.get("handoffReviews", []) if item.get("reviewStatus") == "needs_revision"),
    }, ensure_ascii=False, indent=2))
    return 0


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_legal_evidence_package_template(review_template: Dict[str, Any]) -> Dict[str, Any]:
    risk_codes = [
        risk.get("code")
        for risk in review_template.get("riskReviews") or []
        if risk.get("code") in EVIDENCE_REQUIREMENTS
    ]
    documents = []
    risk_requirements = []
    for code in risk_codes:
        requirement = EVIDENCE_REQUIREMENTS[code]
        risk_requirements.append({
            "riskCode": code,
            "riskTitle": requirement["riskTitle"],
            "minimumSatisfiedCount": requirement["minimumSatisfiedCount"],
            "requiredEvidenceTypes": requirement["evidenceTypes"],
            "status": "missing",
            "missingEvidenceTypes": requirement["evidenceTypes"],
        })
        for evidence_type in requirement["evidenceTypes"]:
            documents.append(_blank_document(code, evidence_type))
    return {
        "schemaVersion": "realytics-legal-evidence-package-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectId": review_template.get("projectId"),
        "runId": review_template.get("runId"),
        "policy": {
            "nonFabrication": True,
            "sourceBackedRequired": True,
            "emptyRecordsDoNotClearRisks": True,
            "resolvedRequiresAllRequiredEvidenceTypes": True,
        },
        "riskRequirements": risk_requirements,
        "documents": documents,
        "reviewActionOutputContract": {
            "resolved": "Generated only when every required evidence type for a risk has source metadata.",
            "needs_revision": "Generated when any required evidence type is missing or lacks source metadata.",
            "waived": "Never generated automatically from this package.",
        },
    }


def _blank_document(risk_code: str, evidence_type: str) -> Dict[str, Any]:
    return {
        "evidenceId": "",
        "riskCode": risk_code,
        "evidenceType": evidence_type,
        "documentTitle": "",
        "sourceName": "",
        "sourceType": "manual_upload",
        "sourceUrlOrEvidenceId": "",
        "fileName": "",
        "fileHash": "",
        "issuedAt": "",
        "baseDate": "",
        "reviewedBy": "",
        "reviewedAt": "",
        "confidence": 0,
        "summary": "",
        "sourceLocators": [
            {
                "sourceType": "document",
                "sourceUrlOrEvidenceId": "",
                "pageNumber": None,
                "table": "",
                "row": "",
                "column": "",
                "fieldName": evidence_type,
                "textExcerpt": "",
                "locatorPrecision": "document_page_or_cell",
            }
        ],
    }


def build_review_actions_from_legal_evidence_package(
    review_template: Dict[str, Any],
    evidence_package: Dict[str, Any],
) -> Dict[str, Any]:
    evidence_by_risk = _index_evidence(evidence_package)
    risk_reviews = []
    status_by_risk_code: Dict[str, str] = {}
    for risk in review_template.get("riskReviews") or []:
        code = str(risk.get("code") or "")
        if code not in EVIDENCE_REQUIREMENTS:
            risk_reviews.append(_copy_needs_revision_risk(risk, "Risk is outside legal evidence package scope."))
            continue
        evaluation = _evaluate_risk_evidence(code, evidence_by_risk.get(code, {}))
        status_by_risk_code[code] = evaluation["status"]
        if evaluation["status"] == "complete":
            risk_reviews.append({
                "riskId": risk.get("riskId"),
                "code": code,
                "reviewStatus": "resolved",
                "reviewedBy": _first_reviewer(evaluation["documents"]),
                "comment": EVIDENCE_REQUIREMENTS[code]["resolvedComment"],
                "evidenceIds": evaluation["evidenceIds"],
                "sourceLocators": evaluation["sourceLocators"],
            })
        else:
            risk_reviews.append({
                "riskId": risk.get("riskId"),
                "code": code,
                "reviewStatus": "needs_revision",
                "reviewedBy": "legal_reviewer",
                "comment": "Missing legal evidence types: " + ", ".join(evaluation["missingEvidenceTypes"]),
                "evidenceIds": evaluation["evidenceIds"],
                "sourceLocators": evaluation["sourceLocators"],
            })

    all_legal_complete = all(
        status_by_risk_code.get(code) == "complete"
        for code in EVIDENCE_REQUIREMENTS
        if any(risk.get("code") == code for risk in review_template.get("riskReviews") or [])
    )
    handoff_reviews = []
    for handoff in review_template.get("handoffReviews") or []:
        if handoff.get("handoffType") in LEGAL_HANDOFF_TYPES and all_legal_complete:
            evidence_ids = sorted({
                evidence_id
                for action in risk_reviews
                for evidence_id in action.get("evidenceIds", [])
            })
            source_locators = [
                locator
                for action in risk_reviews
                for locator in action.get("sourceLocators", [])
            ]
            handoff_reviews.append({
                "handoffId": handoff.get("handoffId"),
                "handoffType": handoff.get("handoffType"),
                "reviewStatus": "approved",
                "reviewedBy": "legal_reviewer",
                "comment": "Legal/permit/covenant evidence package is complete for this handoff.",
                "evidenceIds": evidence_ids,
                "sourceLocators": source_locators,
            })
        else:
            handoff_reviews.append({
                "handoffId": handoff.get("handoffId"),
                "handoffType": handoff.get("handoffType"),
                "reviewStatus": "needs_revision",
                "reviewedBy": "credit_officer",
                "comment": _handoff_needs_revision_comment(handoff, all_legal_complete),
                "evidenceIds": [],
                "sourceLocators": [],
            })

    return {
        "schemaVersion": "realytics-pilot-review-actions-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectId": review_template.get("projectId"),
        "runId": review_template.get("runId"),
        "sourceEvidencePackageVersion": evidence_package.get("schemaVersion"),
        "reviewedAt": datetime.now(timezone.utc).isoformat(),
        "handoffReviews": handoff_reviews,
        "riskReviews": risk_reviews,
    }


def _index_evidence(evidence_package: Dict[str, Any]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    indexed: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for document in evidence_package.get("documents") or []:
        if not _is_source_backed(document):
            continue
        risk_code = str(document.get("riskCode") or "")
        evidence_type = str(document.get("evidenceType") or "")
        indexed.setdefault(risk_code, {}).setdefault(evidence_type, []).append(document)
    return indexed


def _is_source_backed(document: Dict[str, Any]) -> bool:
    return bool(
        document.get("evidenceId")
        and document.get("sourceName")
        and document.get("sourceUrlOrEvidenceId")
        and (document.get("issuedAt") or document.get("baseDate"))
    )


def _evaluate_risk_evidence(
    risk_code: str,
    evidence_by_type: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    required_types = EVIDENCE_REQUIREMENTS[risk_code]["evidenceTypes"]
    missing = [evidence_type for evidence_type in required_types if not evidence_by_type.get(evidence_type)]
    documents = [
        document
        for evidence_type in required_types
        for document in evidence_by_type.get(evidence_type, [])
    ]
    evidence_ids = sorted({document.get("evidenceId") for document in documents if document.get("evidenceId")})
    source_locators = [
        locator
        for document in documents
        for locator in _document_locators(document)
    ]
    return {
        "status": "complete" if not missing else "missing",
        "missingEvidenceTypes": missing,
        "documents": documents,
        "evidenceIds": evidence_ids,
        "sourceLocators": source_locators,
    }


def _document_locators(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    locators = []
    for locator in document.get("sourceLocators") or []:
        if not isinstance(locator, dict):
            continue
        enriched = {
            **locator,
            "sourceUrlOrEvidenceId": locator.get("sourceUrlOrEvidenceId") or document.get("sourceUrlOrEvidenceId"),
            "sourceName": locator.get("sourceName") or document.get("sourceName"),
            "evidenceId": document.get("evidenceId"),
            "evidenceType": document.get("evidenceType"),
            "riskCode": document.get("riskCode"),
        }
        locators.append(enriched)
    if not locators:
        locators.append({
            "sourceType": document.get("sourceType") or "manual_upload",
            "sourceName": document.get("sourceName"),
            "sourceUrlOrEvidenceId": document.get("sourceUrlOrEvidenceId"),
            "evidenceId": document.get("evidenceId"),
            "evidenceType": document.get("evidenceType"),
            "riskCode": document.get("riskCode"),
            "locatorPrecision": "evidence_record",
        })
    return locators


def _first_reviewer(documents: List[Dict[str, Any]]) -> str:
    for document in documents:
        if document.get("reviewedBy"):
            return str(document["reviewedBy"])
    return "legal_reviewer"


def _copy_needs_revision_risk(risk: Dict[str, Any], comment: str) -> Dict[str, Any]:
    return {
        "riskId": risk.get("riskId"),
        "code": risk.get("code"),
        "reviewStatus": "needs_revision",
        "reviewedBy": "credit_officer",
        "comment": comment,
        "evidenceIds": [],
        "sourceLocators": [],
    }


def _handoff_needs_revision_comment(handoff: Dict[str, Any], all_legal_complete: bool) -> str:
    if handoff.get("handoffType") in LEGAL_HANDOFF_TYPES:
        return "Legal evidence package is incomplete; keep this handoff in needs_revision."
    return "This handoff is outside the legal evidence package scope and still requires separate evidence/review."


if __name__ == "__main__":
    raise SystemExit(main())
