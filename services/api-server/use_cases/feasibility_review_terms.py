"""Validated working terms for the existing human-reviewed feasibility save."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
import re
from typing import Any

SCHEDULE_KEYS = (
    "land_purchase", "permit_approval", "construction_start", "presale_start",
    "completion", "loan_maturity",
)
TERM_GROUPS = {"schedule", "financing"}
PRIORITIES = {"senior", "mezzanine", "junior", "equity", "bridge", "other"}
FINANCING_KEYS = {
    "tranche_id", "name", "priority", "amount", "interest_rate", "fee_rate",
    "term_months", "repayment_source",
}


def _first(row: dict, *keys: str):
    # An explicit canonical null/zero takes precedence over stale legacy aliases.
    return next((row[key] for key in keys if key in row), None)


def _schedule_value(value: Any):
    if isinstance(value, dict):
        value = _first(value, "planned_date", "plannedDate", "value", "month_index")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f"M{value:g}"
    return value if isinstance(value, str) else None


def _financing_rows(raw: Any):
    rows = raw if isinstance(raw, list) else []
    used = set()
    explicit = {str(row["tranche_id"]) for row in rows if isinstance(row, dict) and row.get("tranche_id")}
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        identity = str(row.get("tranche_id") or "")
        if not identity or identity in used:
            identity = f"row-{index}"
            while identity in used or identity in explicit:
                identity += "-legacy"
        used.add(identity)
        yield identity, row


def reviewed_terms(project: dict) -> dict:
    schedule = {key: None for key in SCHEDULE_KEYS}
    raw = project.get("schedule")
    if isinstance(raw, dict):
        for key in SCHEDULE_KEYS:
            schedule[key] = _schedule_value(raw.get(key))
    elif isinstance(raw, list):
        for row in raw:
            if isinstance(row, dict):
                key = row.get("key") or row.get("name")
                if key in schedule:
                    schedule[key] = _schedule_value(row)
    financing = []
    for identity, row in _financing_rows(project.get("pf_structure")):
        priority = _first(row, "priority")
        priority = {"1": "senior", "2": "mezzanine", "3": "junior", "middle": "mezzanine"}.get(str(priority), priority)
        financing.append({
            "tranche_id": identity,
            "name": _first(row, "name", "tranche") or "",
            "priority": priority if isinstance(priority, str) and priority in PRIORITIES else "",
            "amount": _first(row, "amount"),
            "interest_rate": _first(row, "interest_rate", "interestRate"),
            "fee_rate": _first(row, "fee_rate", "feeRate"),
            "term_months": _first(row, "term_months", "maturityMonths"),
            "repayment_source": _first(row, "repayment_source", "repaymentSource"),
        })
    return {"schedule": schedule, "financing": financing}


def _number(value: Any, key: str, *, integer: bool, low: float, high: float):
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or value < low or value > high or not math.isfinite(value)
            or (integer and value != int(value))):
        raise ValueError(f"{key}: 유효한 범위의 {'정수' if integer else '숫자'}를 입력하세요.")
    return int(value) if integer else value


def validate_terms(terms: Any) -> dict:
    if not isinstance(terms, dict) or terms.keys() - TERM_GROUPS:
        raise ValueError("사업기간·대출 조건 항목을 확인하세요.")
    result = {}
    if "schedule" in terms:
        schedule = terms["schedule"]
        if not isinstance(schedule, dict) or schedule.keys() - set(SCHEDULE_KEYS):
            raise ValueError("사업기간 항목을 확인하세요.")
        clean = {}
        for key, value in schedule.items():
            if value is not None:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError(f"{key}: YYYY-MM-DD 날짜를 입력하세요.")
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"{key}: 존재하는 날짜를 입력하세요.") from exc
            clean[key] = value
        result["schedule"] = clean
    if "financing" in terms:
        rows = terms["financing"]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("대출 조건은 100개 이하의 행이어야 합니다.")
        clean, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or row.keys() - FINANCING_KEYS:
                raise ValueError("대출 조건 항목을 확인하세요.")
            identity, name, priority = row.get("tranche_id"), row.get("name"), row.get("priority")
            if not isinstance(identity, str) or not identity.strip() or len(identity) > 128 or identity in seen:
                raise ValueError("대출 행 식별자가 없거나 중복되었습니다.")
            if not isinstance(name, str) or not name.strip() or len(name) > 200:
                raise ValueError("대출 구분명을 입력하세요(200자 이하).")
            if not isinstance(priority, str) or priority not in PRIORITIES:
                raise ValueError("대출 우선순위를 선택하세요.")
            repayment = row.get("repayment_source")
            if repayment is not None and (not isinstance(repayment, str) or len(repayment) > 1000):
                raise ValueError("상환 재원을 확인하세요(1000자 이하).")
            seen.add(identity)
            clean.append({
                "tranche_id": identity, "name": name.strip(), "priority": priority,
                "amount": _number(row.get("amount"), "대출금액", integer=True, low=0, high=9_007_199_254_740_991),
                "interest_rate": _number(row.get("interest_rate"), "금리", integer=False, low=0, high=1),
                "fee_rate": _number(row.get("fee_rate"), "수수료율", integer=False, low=0, high=1),
                "term_months": _number(row.get("term_months"), "대출기간", integer=True, low=1, high=1200),
                "repayment_source": repayment.strip() or None if isinstance(repayment, str) else None,
            })
        result["financing"] = clean
    return result


def patch_terms(project: dict, terms: dict) -> dict:
    """Patch working inputs while preserving unknown rows, attributes and provenance."""
    patch = {}
    if "schedule" in terms:
        changes = terms["schedule"]
        raw = deepcopy(project.get("schedule"))
        if isinstance(raw, list):
            found = set()
            for row in raw:
                if not isinstance(row, dict):
                    continue
                key = row.get("key") or row.get("name")
                if key in changes:
                    found.add(key)
                    row["planned_date"] = changes[key]
                    for alias in ("plannedDate", "value", "month_index"):
                        if alias in row:
                            row[alias] = None
            raw.extend({"key": key, "planned_date": value} for key, value in changes.items() if key not in found)
        else:
            raw = raw if isinstance(raw, dict) else {}
            for key, value in changes.items():
                if isinstance(raw.get(key), dict):
                    raw[key]["planned_date"] = value
                    for alias in ("plannedDate", "value", "month_index"):
                        if alias in raw[key]:
                            raw[key][alias] = None
                else:
                    # Explicit null survives repository deep merge and clears the date.
                    raw[key] = value
        patch["schedule"] = raw
        updated = reviewed_terms({**project, **patch})["schedule"]
        start, completion = updated["construction_start"], updated["completion"]
        if (isinstance(start, str) and isinstance(completion, str)
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", start)
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", completion)
                and completion < start):
            raise ValueError("준공일은 착공일보다 빠를 수 없습니다.")
    if "financing" in terms:
        previous = dict(_financing_rows(project.get("pf_structure")))
        rows = []
        aliases = {"interestRate": "interest_rate", "feeRate": "fee_rate",
                   "maturityMonths": "term_months", "repaymentSource": "repayment_source", "tranche": "name"}
        for row in terms["financing"]:
            saved = {**deepcopy(previous.get(row["tranche_id"], {})), **row}
            # Legacy consumers must see the same explicit zero/null as canonical fields.
            for alias, canonical in aliases.items():
                if alias in saved:
                    saved[alias] = row[canonical]
            rows.append(saved)
        patch["pf_structure"] = rows
    return patch
