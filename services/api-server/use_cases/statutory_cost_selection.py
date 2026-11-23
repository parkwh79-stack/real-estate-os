from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import isclose, isfinite
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from agents.cash_flow_engine import cash_flow_cost_schedule_for_review
from agents.cash_flow_mapper import statutory_cost_key_from_label
from core.cash_flow_periods import require_monthly_financing_period
from use_cases.cash_flow import CASH_FLOW_REQUEST_TYPE, _external_candidate_request
from use_cases.cash_flow_dependencies import financial_input_hash
from use_cases.school_site_law_basis import SchoolSiteEvent, SchoolSiteLawSelection, resolve_school_site_law_basis
from use_cases.statutory_cost_review import STATUTORY_COST_CATALOG


StatutoryCostKey = Literal["school_site_charge", "building_artwork"]
ArchiveId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RunId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DocumentLocation = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
AmountKrw = Annotated[int, Field(gt=0, le=1_000_000_000_000_000)]
AmountKind = Literal["estimate", "notified", "paid"]


class StatutoryCostPaymentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    amountKrw: AmountKrw
    archiveId: ArchiveId
    location: DocumentLocation
    paidOn: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]

    @model_validator(mode="after")
    def validate_payment_date(self) -> StatutoryCostPaymentEvidence:
        try:
            paid_date = date.fromisoformat(self.paidOn)
        except ValueError as exc:
            raise ValueError("실제 납부일을 연-월-일로 입력하세요.") from exc
        if paid_date > datetime.now(timezone(timedelta(hours=9))).date():
            raise ValueError("실제 납부일은 미래 날짜일 수 없습니다.")
        return self


class StatutoryCostChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: StatutoryCostKey
    amountKrw: AmountKrw | None = None
    amountKind: AmountKind | None = None
    inclusion: Literal["included", "additional", "not_applicable"]
    archiveId: ArchiveId
    location: DocumentLocation
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
    paymentMonthIndex: int | None = Field(default=None, ge=0, le=600)
    paymentEvidence: StatutoryCostPaymentEvidence | None = None

    @model_validator(mode="after")
    def validate_amount_and_timing(self) -> StatutoryCostChoice:
        if self.inclusion == "not_applicable":
            if any(value is not None for value in (self.amountKrw, self.amountKind, self.paymentMonthIndex)):
                raise ValueError("비적용 근거를 기록할 때는 금액과 월차를 비워 주세요.")
        elif self.amountKrw is None or self.amountKind is None:
            raise ValueError("반영할 비용의 금액과 구분을 확인하세요.")
        if self.inclusion == "additional" and self.paymentMonthIndex is None:
            raise ValueError("추가 비용의 납부 시점(M월)을 지정하세요.")
        if self.paymentEvidence and (self.amountKind == "paid" or self.inclusion == "not_applicable"):
            raise ValueError("실납부 증빙은 추정액이나 고지액을 채택할 때 별도로 기록하세요.")
        return self


class StatutoryCostSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    baseCalculationRunId: RunId
    expectedFinancialInputHash: ArchiveId
    previousReviewCalculationRunId: RunId | None = None
    items: list[StatutoryCostChoice] = Field(min_length=1, max_length=len(STATUTORY_COST_CATALOG))
    schoolSiteEvent: SchoolSiteLawSelection | None = None

    @model_validator(mode="after")
    def require_distinct_costs(self) -> StatutoryCostSelection:
        if len({choice.key for choice in self.items}) != len(self.items):
            raise ValueError("같은 종류의 비용은 한 번만 선택하세요.")
        if self.schoolSiteEvent:
            if not any(choice.key == "school_site_charge" for choice in self.items):
                raise ValueError("학교용지부담금 선택에만 해당 사건을 연결할 수 있습니다.")
            if self.schoolSiteEvent.status != "unknown" and not self.schoolSiteEvent.expectedBasisHash:
                raise ValueError("입력한 사건일의 법령 판본을 먼저 조회하세요.")
        return self


class StatutoryCostBasisConflict(ValueError):
    pass


def reviewable_statutory_basis(record: dict[str, Any]) -> bool:
    snapshot = record.get("inputSnapshot")
    uses = snapshot.get("sourcesAndUses") if isinstance(snapshot, dict) else None
    if not (
        record.get("engineKey") == "cash_flow_engine"
        and record.get("status") == "completed"
        and record.get("financialInputHash")
        and isinstance(snapshot, dict)
        and snapshot.get("requestType") == CASH_FLOW_REQUEST_TYPE
        and snapshot.get("financialInputHash") == record["financialInputHash"]
        and isinstance(uses, dict)
        and isinstance(uses.get("cost"), dict)
        and "capturedWorkbookCashFlow" not in snapshot
        and "statutoryCostSelection" not in snapshot
    ):
        return False
    try:
        require_monthly_financing_period(snapshot)
    except ValueError:
        return False
    return True


def _previous_cost_items(previous_run: dict[str, Any] | None, selection: StatutoryCostSelection) -> dict:
    if not selection.previousReviewCalculationRunId:
        return {}
    snapshot = (previous_run or {}).get("inputSnapshot") or {}
    previous = snapshot.get("statutoryCostSelection") or {}
    if not (
        previous_run
        and previous_run.get("calculationRunId") == selection.previousReviewCalculationRunId
        and previous_run.get("engineKey") == "cash_flow_engine"
        and previous_run.get("status") == "completed"
        and previous.get("schemaVersion") == "realytics-statutory-cost-selection-v1"
        and previous.get("analysisOnly") is True
        and previous.get("baseCalculationRunId") == selection.baseCalculationRunId
        and previous.get("baseFinancialInputHash") == selection.expectedFinancialInputHash
    ):
        raise StatutoryCostBasisConflict("같은 기준 현금흐름에서 저장한 직전 법정 비용 검토안을 선택하세요.")
    items = {item["key"]: item for item in previous["items"]}
    if not any(choice.key in items for choice in selection.items):
        raise StatutoryCostBasisConflict("직전 검토안과 이어지는 법정 비용 항목을 선택하세요.")
    return items


def _cost_document_locator(archive_id: str, location: str, documents: dict) -> dict:
    document = documents.get(archive_id)
    if not document or document.get("archiveId") != archive_id:
        raise ValueError("현재 프로젝트에 보관된 근거 문서를 다시 선택하세요.")
    return {"archiveId": archive_id, "fileName": document["fileName"], "location": location}


def _adoption_record(item: dict) -> dict:
    return {field: item[field] for field in ("amountKind", "amountKrw", "inclusion", "paymentMonthIndex", "sourceLocator")}


def _amount_record(item: dict) -> dict:
    return {field: item[field] for field in ("amountKrw", "paymentMonthIndex", "sourceLocator")}


def _reconcile_cost_amounts(item: dict, previous: dict | None, payment: StatutoryCostPaymentEvidence | None,
                            documents: dict) -> None:
    amounts = dict((previous or {}).get("amountRecords") or {})
    previous_kind = previous.get("amountKind") if previous else None
    if previous_kind is not None and previous_kind not in amounts:
        amounts[previous_kind] = _amount_record(previous)
    kind = item["amountKind"]
    if kind is not None:
        adopted = _amount_record(item)
        prior_amount = amounts.get(kind) or {}
        if (kind == "paid" and prior_amount.get("paidOn") and prior_amount["amountKrw"] == adopted["amountKrw"]
                and prior_amount["sourceLocator"] == adopted["sourceLocator"]):
            adopted["paidOn"] = prior_amount["paidOn"]
        amounts[kind] = adopted
    if payment:
        amounts["paid"] = {
            "amountKrw": payment.amountKrw,
            "paymentMonthIndex": None,
            "paidOn": payment.paidOn,
            "sourceLocator": _cost_document_locator(payment.archiveId, payment.location, documents),
        }
    if amounts:
        item["amountRecords"] = amounts
    if previous:
        item["previousAdoption"] = _adoption_record(previous)


def review_statutory_cost_cash_flow(
    project: dict[str, Any],
    base_run: dict[str, Any],
    selection: StatutoryCostSelection,
    documents: dict[str, dict[str, Any]],
    previous_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adopt selected costs in an isolated analysis tied to a saved cash flow."""
    if (base_run.get("calculationRunId") != selection.baseCalculationRunId
            or not reviewable_statutory_basis(base_run)):
        raise StatutoryCostBasisConflict("월차와 실효 입력이 저장된 기준 현금흐름을 선택하세요.")
    source = dict(base_run["inputSnapshot"])
    stored_hash = source.pop("financialInputHash")
    if stored_hash != selection.expectedFinancialInputHash or financial_input_hash(source) != stored_hash:
        raise StatutoryCostBasisConflict("기준 실행의 입력을 확인할 수 없습니다. 최신 계산에서 다시 선택하세요.")

    previous_items = _previous_cost_items(previous_run, selection)
    request = _external_candidate_request(project, source)
    source_metadata = request["sourceMetadata"]
    source_metadata.update(analysisOnly=True, reportEligible=False, sourceType="statutory_cost_review")
    costs = request["sourcesAndUses"]["cost"]
    other_cost = costs.get("otherCost") or 0
    total_cost = costs.get("totalProjectCost")
    if not isinstance(total_cost, (int, float)) or not isfinite(total_cost) or total_cost <= 0:
        raise StatutoryCostBasisConflict("기준 계산의 총사업비가 확인되어야 합니다.")
    schedule, provenance = cash_flow_cost_schedule_for_review(source)
    if not isclose(sum(sum(row.values()) for row in schedule), total_cost, rel_tol=1e-9, abs_tol=1e-6):
        raise StatutoryCostBasisConflict("기준 계산의 월별 지출 합계와 총사업비를 먼저 대사하세요.")

    base_schedule = source.get("costSchedule") or source.get("cost_schedule") or {}
    raw_rows = base_schedule.get("rows") if isinstance(base_schedule, dict) else base_schedule
    scheduled_statutory_costs = {
        statutory_cost_key_from_label(str(key))
        for row in raw_rows or ()
        if isinstance(row, dict) and isinstance(row.get("sourceBreakdown"), dict)
        for key, amount in row["sourceBreakdown"].items()
        if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount != 0
    }

    items = []
    additions = []
    included_total = 0
    for choice in sorted(selection.items, key=lambda item: item.key):
        locator = _cost_document_locator(choice.archiveId, choice.location, documents)
        month = choice.paymentMonthIndex
        if month is not None and month >= len(schedule):
            raise ValueError(f"{STATUTORY_COST_CATALOG[choice.key]} 납부 시점은 M0~M{len(schedule) - 1} 안에서 선택하세요.")
        if choice.inclusion == "additional":
            if choice.key in scheduled_statutory_costs:
                raise ValueError(f"기준 일정에 {STATUTORY_COST_CATALOG[choice.key]} 항목이 있습니다. 기존 비용 포함으로 검토하세요.")
            additions.append({
                "monthIndex": month, "otherCost": choice.amountKrw, "sourceLocator": locator,
                "sourceBreakdown": {STATUTORY_COST_CATALOG[choice.key]: choice.amountKrw},
            })
        elif choice.inclusion == "included":
            included_total += choice.amountKrw
        item = {
            **choice.model_dump(exclude={"archiveId", "location", "paymentEvidence"}),
            "label": STATUTORY_COST_CATALOG[choice.key],
            "sourceLocator": locator,
            "reviewStatus": "analyst_selected",
            "applicability": "not_applicable" if choice.inclusion == "not_applicable" else "applicable",
        }
        _reconcile_cost_amounts(item, previous_items.get(choice.key), choice.paymentEvidence, documents)
        items.append(item)
    if included_total > other_cost or included_total > sum(row["otherCost"] for row in schedule) + 0.01:
        raise ValueError("이미 포함된 비용의 합계가 기준 계산의 기타 사업비를 초과합니다.")

    school_cost = next((item for item in items if item["key"] == "school_site_charge"), None)
    school_event = selection.schoolSiteEvent or SchoolSiteLawSelection()
    if school_cost is not None:
        event_source = None
        if school_event.archiveId:
            document = documents.get(school_event.archiveId)
            if not document or document.get("archiveId") != school_event.archiveId:
                raise ValueError("현재 프로젝트의 사업 사건 증빙을 확인하세요.")
            event_source = {**document, "location": school_event.location}
        law_basis = resolve_school_site_law_basis(SchoolSiteEvent.model_validate(
            school_event.model_dump(exclude={"expectedBasisHash"}),
        ), event_source)
        if school_event.expectedBasisHash and law_basis["basisHash"] != school_event.expectedBasisHash:
            raise StatutoryCostBasisConflict("법령 판본 또는 사업 사건 근거가 바뀌었습니다. 사건일 판본을 다시 조회하세요.")
        school_cost.update(legalBasis=law_basis, applicability="unknown")

    additional_cost = sum(row["otherCost"] for row in additions)
    if additions:
        # Keep every original month's allocation, including estimated baseline
        # costs, and add each supported amount only to its selected month.
        costs["otherCost"] = other_cost + additional_cost
        costs["totalProjectCost"] = total_cost + additional_cost
        candidate_schedule = request.get("costSchedule") or request.get("cost_schedule") or {}
        if provenance["sourceMode"] == "benchmark_estimate":
            rows = [{"monthIndex": index, **row} for index, row in enumerate(schedule)]
            candidate_schedule = {}
        elif isinstance(candidate_schedule, list):
            rows, candidate_schedule = candidate_schedule, {}
        else:
            rows = candidate_schedule["rows"]
        request.pop("cost_schedule", None)
        request["costSchedule"] = {
            **candidate_schedule,
            "rows": rows + additions,
            "approvalStatus": "unapproved",
            "sourceLocator": candidate_schedule.get("sourceLocator") or {"calculationRunId": selection.baseCalculationRunId},
        }
    request["statutoryCostSelection"] = {
        "schemaVersion": "realytics-statutory-cost-selection-v1",
        "baseCalculationRunId": selection.baseCalculationRunId,
        "baseFinancialInputHash": stored_hash,
        "baselineCostSchedule": provenance,
        "items": items,
        "additionalCostKrw": additional_cost,
        "analysisOnly": True,
    }
    if selection.previousReviewCalculationRunId:
        request["statutoryCostSelection"]["previousReviewCalculationRunId"] = selection.previousReviewCalculationRunId
    request["inputEvidenceIds"] = list(dict.fromkeys([
        *(request.get("inputEvidenceIds") or request.get("evidenceIds") or []),
        *(item.archiveId for item in selection.items),
        *(item.paymentEvidence.archiveId for item in selection.items if item.paymentEvidence),
        *([school_event.archiveId] if school_event.archiveId else []),
    ]))
    return request
