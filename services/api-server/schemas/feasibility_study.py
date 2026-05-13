from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


FEASIBILITY_STUDY_SCHEMA_VERSION = "realytics-feasibility-study-v2"
FEASIBILITY_WORKBOOK_VERSION = "realytics-fs-xlsx-v1"


class AssetType(str, Enum):
    apartment = "apartment"
    officetel = "officetel"
    knowledge_industry_center = "knowledge_industry_center"
    mixed_use = "mixed_use"
    commercial = "commercial"
    logistics = "logistics"
    lodging = "lodging"
    residential_land = "residential_land"
    other = "other"


class AmountUnit(str, Enum):
    krw = "KRW"
    krw_million = "KRW_MILLION"
    sqm = "SQM"
    py = "PY"
    percent = "PERCENT"
    count = "COUNT"
    month = "MONTH"


class ExcelCellRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_name: str = Field(..., description="Worksheet name used by the workbook template.")
    cell: Optional[str] = Field(None, description="A1 cell reference when the value maps to one fixed cell.")
    named_range: Optional[str] = Field(None, description="Stable Excel defined name used for import/export.")
    table_name: Optional[str] = Field(None, description="Excel table name when the value belongs to a repeated row.")
    column_name: Optional[str] = Field(None, description="Excel table column name for repeated rows.")


class FieldProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document: Optional[str] = None
    source_type: Optional[Literal["pdf", "docx", "xlsx", "manual", "api", "mcp"]] = None
    page: Optional[int] = None
    sheet_name: Optional[str] = None
    cell: Optional[str] = None
    named_range: Optional[str] = None
    raw_text: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0, le=1)


class FormulaBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formula: Optional[str] = Field(None, description="Excel formula without leading apostrophe.")
    formula_type: Literal["input", "excel_formula", "api_formula", "derived"] = "input"
    depends_on: List[str] = Field(default_factory=list)
    editable: bool = True
    locked_in_template: bool = False


class FeasibilityValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value: Optional[float | str | bool] = None
    unit: Optional[AmountUnit] = None
    excel: Optional[ExcelCellRef] = None
    formula: Optional[FormulaBinding] = None
    provenance: Optional[FieldProvenance] = None
    notes: Optional[str] = None


class SiteInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: Optional[str] = None
    land_area_sqm: Optional[FeasibilityValue] = None
    zoning: Optional[str] = None
    legal_far: Optional[FeasibilityValue] = None
    legal_bcr: Optional[FeasibilityValue] = None
    acquired_land_ratio: Optional[FeasibilityValue] = None


class ProgramInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gross_floor_area_sqm: Optional[FeasibilityValue] = None
    saleable_area_sqm: Optional[FeasibilityValue] = None
    contract_area_sqm: Optional[FeasibilityValue] = None
    floors_above_ground: Optional[FeasibilityValue] = None
    floors_below_ground: Optional[FeasibilityValue] = None
    parking_count: Optional[FeasibilityValue] = None
    unit_count: Optional[FeasibilityValue] = None


class ProductMixRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    asset_type: AssetType
    product_name: str
    unit_count: Optional[float] = None
    exclusive_area_sqm: Optional[float] = None
    saleable_area_sqm: Optional[float] = None
    contract_area_sqm: Optional[float] = None
    avg_sale_price_per_sqm: Optional[float] = None
    avg_sale_price_per_py: Optional[float] = None
    revenue: Optional[float] = None
    excel_row_id: Optional[str] = None
    provenance: Optional[FieldProvenance] = None


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    category: str
    amount: Optional[float] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    unit: AmountUnit = AmountUnit.krw
    excel: Optional[ExcelCellRef] = None
    formula: Optional[FormulaBinding] = None
    provenance: Optional[FieldProvenance] = None


class FinancingTranche(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tranche_id: str
    name: str
    priority: Literal["senior", "middle", "junior", "equity", "bridge", "other"]
    amount: Optional[float] = None
    interest_rate: Optional[float] = None
    fee_rate: Optional[float] = None
    term_months: Optional[int] = None
    repayment_source: Optional[str] = None
    excel_row_id: Optional[str] = None


class ScheduleMilestone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    planned_date: Optional[str] = None
    actual_date: Optional[str] = None
    month_index: Optional[int] = None
    excel: Optional[ExcelCellRef] = None
    provenance: Optional[FieldProvenance] = None


class ScenarioAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    label: str
    sales_price_factor: float = 1.0
    construction_cost_factor: float = 1.0
    interest_rate_addition: float = 0.0
    sales_rate: Optional[float] = None
    delay_months: int = 0


class WorkbookSheetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_name: str
    role: Literal[
        "cover",
        "inputs",
        "product_mix",
        "costs",
        "financing",
        "monthly_cf",
        "sensitivity",
        "summary",
        "checks",
        "export_map",
    ]
    required: bool = True
    description: Optional[str] = None


class WorkbookMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workbook_version: str = FEASIBILITY_WORKBOOK_VERSION
    template_name: str = "REALYTICS_Feasibility_Study_Template"
    formula_policy: Literal["preserve_excel_formulas", "api_calculates", "hybrid"] = "preserve_excel_formulas"
    unit_scale: AmountUnit = AmountUnit.krw_million
    sheets: List[WorkbookSheetSpec] = Field(default_factory=list)
    named_ranges: Dict[str, str] = Field(
        default_factory=dict,
        description="Map schema paths to Excel defined names for robust import/export.",
    )


class FeasibilityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_revenue: Optional[float] = None
    total_cost: Optional[float] = None
    development_profit: Optional[float] = None
    profit_margin: Optional[float] = None
    profit_on_cost: Optional[float] = None
    break_even_sale_rate: Optional[float] = None
    required_equity: Optional[float] = None
    max_minus: Optional[float] = None
    ltv: Optional[float] = None
    ltc: Optional[float] = None
    exit_sale_rate: Optional[float] = None


DEFAULT_FEASIBILITY_WORKBOOK_SHEETS = [
    WorkbookSheetSpec(sheet_name="00_Control", role="cover", description="Version, project metadata, and import checks"),
    WorkbookSheetSpec(sheet_name="01_Assumptions", role="inputs", description="Site, program, schedule, and high-level assumptions"),
    WorkbookSheetSpec(sheet_name="02_ProductMix", role="product_mix", description="Saleable product mix and price assumptions"),
    WorkbookSheetSpec(sheet_name="03_Costs", role="costs", description="Land, direct, indirect, tax, fee, and contingency costs"),
    WorkbookSheetSpec(sheet_name="04_Financing", role="financing", description="Equity, bridge, PF tranches, rates, fees, and terms"),
    WorkbookSheetSpec(sheet_name="05_MonthlyCF", role="monthly_cf", description="Monthly cash-in/out with formula-driven cumulative cash"),
    WorkbookSheetSpec(sheet_name="06_Sensitivity", role="sensitivity", description="Formula-linked downside and upside scenarios"),
    WorkbookSheetSpec(sheet_name="07_Summary", role="summary", description="Developer and PF review KPIs"),
    WorkbookSheetSpec(sheet_name="99_Checks", role="checks", description="Formula integrity, balance checks, and import validation"),
    WorkbookSheetSpec(sheet_name="_ExportMap", role="export_map", description="Hidden schema-path to named-range mapping"),
]


DEFAULT_NAMED_RANGES = {
    "project_name": "fs_project_name",
    "asset_type": "fs_asset_type",
    "site.land_area_sqm.value": "fs_land_area_sqm",
    "site.legal_far.value": "fs_legal_far",
    "site.legal_bcr.value": "fs_legal_bcr",
    "program.gross_floor_area_sqm.value": "fs_gross_floor_area_sqm",
    "program.saleable_area_sqm.value": "fs_saleable_area_sqm",
    "metrics.total_revenue": "fs_total_revenue",
    "metrics.total_cost": "fs_total_cost",
    "metrics.development_profit": "fs_development_profit",
    "metrics.profit_margin": "fs_profit_margin",
    "metrics.profit_on_cost": "fs_profit_on_cost",
    "metrics.break_even_sale_rate": "fs_break_even_sale_rate",
    "metrics.max_minus": "fs_max_minus",
    "metrics.ltv": "fs_ltv",
    "metrics.ltc": "fs_ltc",
    "metrics.exit_sale_rate": "fs_exit_sale_rate",
}


def default_workbook_mapping() -> WorkbookMapping:
    return WorkbookMapping(
        sheets=DEFAULT_FEASIBILITY_WORKBOOK_SHEETS,
        named_ranges=DEFAULT_NAMED_RANGES,
    )


class FeasibilityStudyV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = FEASIBILITY_STUDY_SCHEMA_VERSION
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    asset_type: AssetType = AssetType.other
    currency: Literal["KRW"] = "KRW"
    site: SiteInputs = Field(default_factory=SiteInputs)
    program: ProgramInputs = Field(default_factory=ProgramInputs)
    product_mix: List[ProductMixRow] = Field(default_factory=list)
    revenue_items: List[LineItem] = Field(default_factory=list)
    cost_items: List[LineItem] = Field(default_factory=list)
    financing: List[FinancingTranche] = Field(default_factory=list)
    schedule: List[ScheduleMilestone] = Field(default_factory=list)
    scenarios: List[ScenarioAssumption] = Field(default_factory=list)
    workbook: WorkbookMapping = Field(default_factory=default_workbook_mapping)
    metrics: Optional[FeasibilityMetrics] = None
    raw_import: Dict[str, Any] = Field(default_factory=dict)
