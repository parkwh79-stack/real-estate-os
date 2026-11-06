from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class CanonicalField:
    path: str
    label: str
    value_type: str
    owners: tuple[str, ...] = field(default_factory=tuple)
    required_for: tuple[str, ...] = field(default_factory=tuple)
    aliases: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SchemaContract:
    schema_id: str
    version: str
    owner_agent: str
    artifact_key: str
    status: str
    description: str
    fields: tuple[CanonicalField, ...]


COMMON_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField("project.name", "Project name", "string", ("parser", "orchestration", "report"), ("report",), ("사업명", "프로젝트명")),
    CanonicalField("project.type", "Project type", "string", ("parser", "marketability", "feasibility"), ("marketability",), ("사업유형", "상품유형")),
    CanonicalField("land.address", "Legal or site address", "string", ("land", "parser", "legal"), ("land_review",), ("소재지", "주소")),
    CanonicalField("land.pnu", "Parcel Numbering Unit", "string", ("land", "legal", "evidence"), ("land_review", "legal_review"), ("PNU", "필지고유번호")),
    CanonicalField("land.area_sqm", "Land area in square meters", "number", ("land", "feasibility"), ("feasibility",), ("대지면적", "토지면적")),
    CanonicalField("sourceMetadata.sourceName", "Source name", "string", ("parser", "evidence_graph"), ("evidence",), ("출처", "자료명")),
    CanonicalField("sourceMetadata.sourceLocator", "Source locator", "object", ("parser", "evidence_graph"), ("evidence",), ("문서위치", "셀주소", "API row")),
)


SCHEMA_CONTRACTS: tuple[SchemaContract, ...] = (
    SchemaContract(
        schema_id="realytics-feasibility-study-v2",
        version="2.0",
        owner_agent="DevelopmentFeasibilityAgent",
        artifact_key="feasibilityStudy",
        status="active",
        description="Canonical developer-side feasibility schema for business plans, architectural programs, Excel import/export, and PF-readiness handoff.",
        fields=COMMON_FIELDS
        + (
            CanonicalField("program.grossFloorAreaSqm", "Gross floor area", "number", ("feasibility", "parser"), ("feasibility",), ("연면적",)),
            CanonicalField("productMix", "Product mix", "array", ("feasibility", "marketability"), ("feasibility", "marketability"), ("상품구성", "분양면적")),
            CanonicalField("revenue.totalSalesRevenue", "Total sales revenue", "number", ("feasibility", "marketability"), ("cash_flow", "pf_review"), ("총분양수입", "매출")),
            CanonicalField("cost.totalProjectCost", "Total project cost", "number", ("feasibility", "cash_flow"), ("pf_review",), ("총사업비", "총투자비")),
            CanonicalField("financing.pfAmount", "PF loan amount", "number", ("pf_structuring", "cash_flow"), ("pf_review",), ("PF금액", "대출한도")),
        ),
    ),
    SchemaContract(
        schema_id="realytics-land-ledger-import-v1",
        version="1.0",
        owner_agent="LandLedgerImportAgent",
        artifact_key="landLedgerImport",
        status="active",
        description="Land ledger import contract for parcels, owners, payment schedules, PNU enrichment, acquisition readiness, and legal evidence handoff.",
        fields=COMMON_FIELDS
        + (
            CanonicalField("parcels[].rawLotNumber", "Raw lot number", "string", ("land",), ("land_review",), ("지번",)),
            CanonicalField("parcels[].normalizedLotNumber", "Normalized lot number", "string", ("land",), ("land_review",), ("정규화지번",)),
            CanonicalField("parcels[].pnu", "Parcel Numbering Unit", "string", ("land", "legal", "evidence"), ("land_review", "legal_review"), ("PNU", "필지고유번호")),
            CanonicalField("parcels[].registeredAreaSqm", "Registered parcel area", "number", ("land", "legal"), ("land_review", "feasibility"), ("공부상면적", "대장면적")),
            CanonicalField("parcels[].officialLandPrice.pricePerSqm", "Official land price per sqm", "number", ("land", "evidence"), ("legal_review", "pf_review"), ("공시지가",)),
            CanonicalField("parcels[].registryEvidence.evidenceId", "Registry evidence ID", "string", ("land", "legal", "evidence"), ("legal_review",), ("등기부", "제한물권증빙")),
        ),
    ),
    SchemaContract(
        schema_id="realytics-marketability-analysis-v2",
        version="2.0",
        owner_agent="MarketabilityAnalysisAgent",
        artifact_key="marketabilityAnalysis",
        status="active",
        description="Marketability contract for saleable assets, comparable evidence, proposed price basis, absorption, discount triggers, and PF exit resilience.",
        fields=COMMON_FIELDS
        + (
            CanonicalField("pricing.proposedPricePerPy", "Proposed sale price per py", "number", ("marketability",), ("marketability",), ("제안분양가", "평당분양가")),
            CanonicalField("pricing.comparableMedianPricePerPy", "Comparable median price per py", "number", ("marketability", "market_evidence"), ("marketability",), ("비교사례중앙값",)),
            CanonicalField("absorptionForecast.completion", "Completion sales rate forecast", "number", ("marketability",), ("cash_flow", "pf_review"), ("준공시분양률", "흡수율")),
            CanonicalField("discountTrigger", "Discount sale trigger table", "array", ("marketability", "pf_structuring"), ("pf_review",), ("할인분양트리거",)),
        ),
    ),
    SchemaContract(
        schema_id="realytics-cash-flow-run-request-v1",
        version="1.0",
        owner_agent="CashFlowEngine",
        artifact_key="cashFlow",
        status="active",
        description="Cash Flow Engine request/result contract for monthly cash flow, Max Minus, PF draw, repayment reserve, and waterfall scenario handoff.",
        fields=COMMON_FIELDS
        + (
            CanonicalField("sourcesAndUses.revenue.totalSalesRevenue", "Total sales revenue", "number", ("cash_flow", "feasibility"), ("cash_flow",), ("총분양수입",)),
            CanonicalField("sourcesAndUses.cost.landCost", "Land cost", "number", ("cash_flow", "land"), ("cash_flow",), ("토지비",)),
            CanonicalField("monthlyCashFlow", "Monthly cash flow rows", "array", ("cash_flow",), ("pf_review",), ("월별현금흐름",)),
            CanonicalField("maxMinus.amount", "Max Minus amount", "number", ("cash_flow",), ("pf_review",), ("최대부족자금", "Max Minus")),
        ),
    ),
    SchemaContract(
        schema_id="realytics-scenario-set-v1",
        version="1.0",
        owner_agent="ScenarioEngineAgent",
        artifact_key="scenarioResult",
        status="active",
        description="Scenario contract for waterfall, unsold LTV, tranche recovery, restructuring cures, and stress/cure reruns.",
        fields=COMMON_FIELDS
        + (
            CanonicalField("waterfallResult.rows", "Waterfall rows", "array", ("scenario", "pf_structuring"), ("pf_review",), ("상환순위", "Waterfall")),
            CanonicalField("unsoldLtvMatrix.rows", "Unsold LTV matrix rows", "array", ("scenario",), ("pf_review",), ("미분양LTV",)),
            CanonicalField("trancheRecovery", "Tranche recovery result", "object", ("scenario", "credit_review"), ("credit_review",), ("Tranche회수율",)),
        ),
    ),
    SchemaContract(
        schema_id="realytics-evidence-record-v1",
        version="1.0",
        owner_agent="EvidenceGraphAgent",
        artifact_key="evidenceGraph",
        status="active",
        description="Evidence contract for document pages, Excel cells, API rows, source metadata, risk rows, report claims, and glossary canonical meaning links.",
        fields=COMMON_FIELDS
        + (
            CanonicalField("evidenceId", "Evidence ID", "string", ("evidence_graph",), ("evidence",), ("증빙ID",)),
            CanonicalField("evidenceType", "Evidence type", "string", ("evidence_graph",), ("evidence",), ("증빙유형",)),
            CanonicalField("hash", "Source file hash", "string", ("source_document", "evidence_graph"), ("audit",), ("SHA256", "파일해시")),
            CanonicalField("confidence", "Evidence confidence", "number", ("evidence_graph",), ("audit",), ("신뢰도",)),
        ),
    ),
    SchemaContract(
        schema_id="realytics-phase11-pf-review-report-v1",
        version="1.0",
        owner_agent="CreditReviewReportAgent",
        artifact_key="phase11Report",
        status="active",
        description="Twenty-section PF review report contract assembled from agent artifacts, risk register, approval gate, and evidence graph.",
        fields=COMMON_FIELDS
        + (
            CanonicalField("cover", "Report cover", "object", ("report",), ("report",), ("표지",)),
            CanonicalField("financialTerms", "Financial terms summary", "object", ("pf_structuring", "report"), ("report",), ("주요금융조건",)),
            CanonicalField("riskManagement", "Risk management controls", "array", ("risk_register", "report"), ("report",), ("리스크관리",)),
            CanonicalField("approvalConditions", "Approval conditions", "array", ("credit_review", "report"), ("report",), ("승인조건",)),
        ),
    ),
)


def list_schema_contracts(status: Optional[str] = None) -> List[Dict[str, Any]]:
    contracts = SCHEMA_CONTRACTS
    if status:
        contracts = tuple(contract for contract in contracts if contract.status == status)
    return [_contract_to_dict(contract, include_fields=False) for contract in contracts]


def get_schema_contract(schema_id: str) -> Optional[Dict[str, Any]]:
    for contract in SCHEMA_CONTRACTS:
        if contract.schema_id == schema_id:
            return _contract_to_dict(contract, include_fields=True)
    return None


def list_canonical_fields(owner: Optional[str] = None, required_for: Optional[str] = None) -> List[Dict[str, Any]]:
    fields: Dict[str, CanonicalField] = {}
    for contract in SCHEMA_CONTRACTS:
        for item in contract.fields:
            if owner and owner not in item.owners:
                continue
            if required_for and required_for not in item.required_for:
                continue
            fields.setdefault(item.path, item)
    return [_field_to_dict(item) for item in sorted(fields.values(), key=lambda f: f.path)]


def find_canonical_field(path: str) -> Optional[Dict[str, Any]]:
    normalized = path.strip()
    for item in list_canonical_fields():
        if item["path"] == normalized:
            return item
    return None


def validate_required_fields(
    schema_id: str,
    payload: Dict[str, Any],
    *,
    required_for: Optional[str] = None,
) -> Dict[str, Any]:
    contract = next((item for item in SCHEMA_CONTRACTS if item.schema_id == schema_id), None)
    if not contract:
        return {"schemaId": schema_id, "status": "unknown_schema", "present": [], "missing": []}

    candidates = [
        item
        for item in contract.fields
        if not required_for or required_for in item.required_for
    ]
    present = [item.path for item in candidates if _has_path(payload, item.path)]
    missing = [item.path for item in candidates if item.path not in present]
    return {
        "schemaId": schema_id,
        "version": contract.version,
        "requiredFor": required_for,
        "status": "complete" if not missing else "missing_required_fields",
        "present": present,
        "missing": missing,
    }


def _contract_to_dict(contract: SchemaContract, *, include_fields: bool) -> Dict[str, Any]:
    data = {
        "schemaId": contract.schema_id,
        "version": contract.version,
        "ownerAgent": contract.owner_agent,
        "artifactKey": contract.artifact_key,
        "status": contract.status,
        "description": contract.description,
        "fieldCount": len(contract.fields),
    }
    if include_fields:
        data["fields"] = [_field_to_dict(item) for item in contract.fields]
    return data


def _field_to_dict(field_item: CanonicalField) -> Dict[str, Any]:
    return {
        "path": field_item.path,
        "label": field_item.label,
        "valueType": field_item.value_type,
        "owners": list(field_item.owners),
        "requiredFor": list(field_item.required_for),
        "aliases": list(field_item.aliases),
    }


def _has_path(payload: Dict[str, Any], path: str) -> bool:
    current: Any = payload
    parts = path.replace("[]", "").split(".")
    for part in parts:
        if isinstance(current, list):
            if not current:
                return False
            return any(_has_path(item, ".".join(parts[parts.index(part) :])) for item in current if isinstance(item, dict))
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return current not in (None, "", [])


__all__ = [
    "CanonicalField",
    "SchemaContract",
    "SCHEMA_CONTRACTS",
    "list_schema_contracts",
    "get_schema_contract",
    "list_canonical_fields",
    "find_canonical_field",
    "validate_required_fields",
]
