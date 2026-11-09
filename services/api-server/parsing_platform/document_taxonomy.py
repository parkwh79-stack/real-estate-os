from __future__ import annotations

"""Canonical, versioned document-type namespace for Parser V2.

The taxonomy owns semantic document types. File/container formats such as PDF
and XLSX belong to adapter compatibility, not this namespace.
"""

from dataclasses import asdict, dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable


DOCUMENT_TAXONOMY_VERSION = "realytics-document-taxonomy-v1"
DOCUMENT_TYPE_SCOPE_LISTED = "listed"
DOCUMENT_TYPE_SCOPE_ALL = "all"
DOCUMENT_TYPE_SCOPES = frozenset(
    {DOCUMENT_TYPE_SCOPE_LISTED, DOCUMENT_TYPE_SCOPE_ALL}
)


class DocumentType(str, Enum):
    ACCOUNT_AGREEMENT = "account_agreement"
    APPRAISAL_REPORT = "appraisal_report"
    BASIC_ARCHITECTURAL_PLAN = "basic_architectural_plan"
    BUDGET = "budget"
    BUSINESS_PLAN = "business_plan"
    CASHFLOW_WORKBOOK = "cashflow_workbook"
    CONSTRUCTION_CONTRACT = "construction_contract"
    CREDIT_REVIEW_MEMO = "credit_review_memo"
    FEASIBILITY_WORKBOOK = "feasibility_workbook"
    FINANCIAL_STATEMENT = "financial_statement"
    GUARANTEE = "guarantee"
    INVESTMENT_COMMITTEE_MEMO = "investment_committee_memo"
    INVESTMENT_MEMORANDUM = "investment_memorandum"
    LAND_EVIDENCE = "land_evidence"
    LAND_LEDGER = "land_ledger"
    LAND_SALE_AGREEMENT = "land_sale_agreement"
    LAND_USE_PLAN = "land_use_plan"
    LEASE_AGREEMENT = "lease_agreement"
    LOAN_AGREEMENT = "loan_agreement"
    MARKET_EVIDENCE = "market_evidence"
    MARKET_REPORT = "market_report"
    OPERATING_STATEMENT = "operating_statement"
    PERMIT = "permit"
    PRESALE_COMPARABLES = "presale_comparables"
    PROGRESS_REPORT = "progress_report"
    REGISTRY = "registry"
    RENT_ROLL = "rent_roll"
    RISK_REPORT_WORKBOOK = "risk_report_workbook"
    SALES_PLAN = "sales_plan"
    SCHEDULE = "schedule"
    SECURITY_AGREEMENT = "security_agreement"
    TERM_SHEET = "term_sheet"
    TRANSACTION_COMPARABLES = "transaction_comparables"
    TRUST_AGREEMENT = "trust_agreement"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DocumentTypeContract:
    document_type: DocumentType
    label: str
    suggested_action: str = "upload_and_parse"
    classifier_enabled: bool = False
    aliases: tuple[str, ...] = ()
    classification_terminal: bool = False

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["documentType"] = self.document_type.value
        payload.pop("document_type")
        payload["suggestedAction"] = payload.pop("suggested_action")
        payload["classifierEnabled"] = payload.pop("classifier_enabled")
        payload["classificationTerminal"] = payload.pop(
            "classification_terminal"
        )
        payload["aliases"] = list(self.aliases)
        return payload


_CONTRACTS = (
    DocumentTypeContract(
        DocumentType.BUSINESS_PLAN,
        "사업계획서 / IR",
        classifier_enabled=True,
    ),
    DocumentTypeContract(
        DocumentType.BASIC_ARCHITECTURAL_PLAN,
        "기본설계 도서",
        classifier_enabled=True,
        aliases=("architectural_plan",),
    ),
    DocumentTypeContract(
        DocumentType.LAND_LEDGER,
        "토지대장 / 토지조서",
        "land_ledger_import",
        classifier_enabled=True,
    ),
    DocumentTypeContract(
        DocumentType.FEASIBILITY_WORKBOOK,
        "사업수지 / 현금흐름 워크북",
        "feasibility_workbook_validate",
        classifier_enabled=True,
    ),
    DocumentTypeContract(
        DocumentType.RISK_REPORT_WORKBOOK,
        "리스크 보고서 워크북",
        "risk_report_workbook_validate",
        classifier_enabled=True,
    ),
    DocumentTypeContract(
        DocumentType.LAND_EVIDENCE,
        "토지 증빙 (공시지가·등기 등)",
        "land_evidence_import",
        classifier_enabled=True,
    ),
    DocumentTypeContract(
        DocumentType.MARKET_EVIDENCE,
        "시장·실거래 자료",
        "market_evidence_attach",
        classifier_enabled=True,
    ),
    DocumentTypeContract(
        DocumentType.UNKNOWN,
        "미분류",
        "manual_review",
        classifier_enabled=True,
        classification_terminal=True,
    ),
    DocumentTypeContract(DocumentType.ACCOUNT_AGREEMENT, "계좌관리 약정"),
    DocumentTypeContract(DocumentType.APPRAISAL_REPORT, "감정평가서"),
    DocumentTypeContract(DocumentType.BUDGET, "예산서"),
    DocumentTypeContract(DocumentType.CASHFLOW_WORKBOOK, "현금흐름 워크북"),
    DocumentTypeContract(DocumentType.CONSTRUCTION_CONTRACT, "공사도급계약"),
    DocumentTypeContract(DocumentType.CREDIT_REVIEW_MEMO, "여신심사 메모"),
    DocumentTypeContract(DocumentType.FINANCIAL_STATEMENT, "재무제표"),
    DocumentTypeContract(DocumentType.GUARANTEE, "보증서"),
    DocumentTypeContract(
        DocumentType.INVESTMENT_COMMITTEE_MEMO,
        "투자심의 메모",
    ),
    DocumentTypeContract(DocumentType.INVESTMENT_MEMORANDUM, "투자설명서"),
    DocumentTypeContract(DocumentType.LAND_SALE_AGREEMENT, "토지매매계약"),
    DocumentTypeContract(DocumentType.LAND_USE_PLAN, "토지이용계획"),
    DocumentTypeContract(DocumentType.LEASE_AGREEMENT, "임대차계약"),
    DocumentTypeContract(DocumentType.LOAN_AGREEMENT, "대출약정"),
    DocumentTypeContract(DocumentType.MARKET_REPORT, "시장조사 보고서"),
    DocumentTypeContract(DocumentType.OPERATING_STATEMENT, "운영수지표"),
    DocumentTypeContract(DocumentType.PERMIT, "인허가 문서"),
    DocumentTypeContract(DocumentType.PRESALE_COMPARABLES, "분양사례"),
    DocumentTypeContract(DocumentType.PROGRESS_REPORT, "공정 보고서"),
    DocumentTypeContract(DocumentType.REGISTRY, "등기 문서"),
    DocumentTypeContract(DocumentType.RENT_ROLL, "임대현황표"),
    DocumentTypeContract(DocumentType.SALES_PLAN, "분양계획"),
    DocumentTypeContract(DocumentType.SCHEDULE, "사업·공정 일정"),
    DocumentTypeContract(DocumentType.SECURITY_AGREEMENT, "담보계약"),
    DocumentTypeContract(DocumentType.TERM_SHEET, "금융조건서"),
    DocumentTypeContract(DocumentType.TRANSACTION_COMPARABLES, "실거래 사례"),
    DocumentTypeContract(DocumentType.TRUST_AGREEMENT, "신탁계약"),
)


DOCUMENT_TYPE_CONTRACTS = MappingProxyType(
    {item.document_type.value: item for item in _CONTRACTS}
)
ALL_CANONICAL_DOCUMENT_TYPES = tuple(
    document_type.value for document_type in DocumentType
)
CLASSIFIER_DOCUMENT_TYPES = tuple(
    item.document_type.value for item in _CONTRACTS if item.classifier_enabled
)
_ALIASES = MappingProxyType(
    {
        alias.casefold(): item.document_type.value
        for item in _CONTRACTS
        for alias in item.aliases
    }
)


def canonical_document_type(value: DocumentType | str, *, strict: bool = True) -> str:
    if isinstance(value, DocumentType):
        return value.value
    normalized = str(value or "").strip().casefold()
    if normalized in DOCUMENT_TYPE_CONTRACTS:
        return normalized
    alias = _ALIASES.get(normalized)
    if alias is not None:
        return alias
    if strict:
        raise ValueError(
            f"Unknown {DOCUMENT_TAXONOMY_VERSION} document type: {value!r}"
        )
    return normalized


def canonical_document_types(
    values: Iterable[DocumentType | str],
    *,
    strict: bool = True,
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        document_type = canonical_document_type(value, strict=strict)
        if document_type and document_type not in seen:
            seen.add(document_type)
            normalized.append(document_type)
    return tuple(normalized)


def document_type_contract(
    value: DocumentType | str,
) -> DocumentTypeContract:
    return DOCUMENT_TYPE_CONTRACTS[canonical_document_type(value)]


def document_taxonomy_catalog() -> dict[str, object]:
    return {
        "schemaVersion": DOCUMENT_TAXONOMY_VERSION,
        "documentTypes": [
            DOCUMENT_TYPE_CONTRACTS[value].to_dict()
            for value in ALL_CANONICAL_DOCUMENT_TYPES
        ],
    }


__all__ = [
    "ALL_CANONICAL_DOCUMENT_TYPES",
    "CLASSIFIER_DOCUMENT_TYPES",
    "DOCUMENT_TAXONOMY_VERSION",
    "DOCUMENT_TYPE_CONTRACTS",
    "DOCUMENT_TYPE_SCOPE_ALL",
    "DOCUMENT_TYPE_SCOPE_LISTED",
    "DOCUMENT_TYPE_SCOPES",
    "DocumentType",
    "DocumentTypeContract",
    "canonical_document_type",
    "canonical_document_types",
    "document_taxonomy_catalog",
    "document_type_contract",
]
