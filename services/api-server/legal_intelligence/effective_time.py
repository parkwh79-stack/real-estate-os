from __future__ import annotations

"""Fail-closed effective-time lineage rules for immutable legal versions."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable


EFFECTIVE_CONTINUITY_POLICIES = frozenset(
    {
        "strict_contiguous",
        "allow_documented_gap",
        "not_applicable",
    }
)


class EffectiveTimeConflict(ValueError):
    """A candidate cannot be attached to the verified effective-time chain."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EffectiveVersionWindow:
    legal_resource_version_id: str
    official_version_identifier: str
    version_sequence: int
    effective_from: date | None
    effective_to: date | None
    valid_time_start: date | None
    valid_time_end: date | None


@dataclass(frozen=True)
class EffectiveVersionCandidate:
    legal_resource_version_id: str
    official_version_identifier: str
    effective_from: date | None
    effective_to: date | None
    valid_time_start: date | None
    valid_time_end: date | None
    declared_predecessor_official_version_identifier: str | None
    continuity_policy: str = "strict_contiguous"
    gap_reason: str | None = None

    def __post_init__(self) -> None:
        if self.continuity_policy not in EFFECTIVE_CONTINUITY_POLICIES:
            raise ValueError(
                "continuity_policy must be one of: "
                + ", ".join(sorted(EFFECTIVE_CONTINUITY_POLICIES))
            )
        _ordered_range(
            self.effective_from,
            self.effective_to,
            "effective-time",
        )
        _ordered_range(
            self.valid_time_start,
            self.valid_time_end,
            "valid-time",
        )
        if (
            self.continuity_policy == "allow_documented_gap"
            and not str(self.gap_reason or "").strip()
        ):
            raise ValueError(
                "allow_documented_gap requires a non-empty gap_reason"
            )


@dataclass(frozen=True)
class EffectiveLineageDecision:
    predecessor_version_id: str | None
    lineage_type: str
    continuity_status: str
    predecessor_effective_to: date | None = None
    successor_effective_from: date | None = None
    derived_predecessor_effective_to: date | None = None
    gap_days: int = 0


def validate_effective_time_lineage(
    existing_versions: Iterable[EffectiveVersionWindow],
    candidate: EffectiveVersionCandidate,
) -> EffectiveLineageDecision:
    """Validate and resolve a candidate against the immutable chain.

    Date ends are inclusive.  Parser/source corrections are allowed only when
    the official version identifier and both legal-time windows are unchanged.
    A new official version must declare the current tip as its predecessor.
    Gaps and overlaps fail closed unless a gap is explicitly documented.
    """

    existing = tuple(
        sorted(existing_versions, key=lambda item: item.version_sequence)
    )
    if len(
        {item.legal_resource_version_id for item in existing}
    ) != len(existing):
        raise EffectiveTimeConflict(
            "duplicate_existing_version",
            "effective-time history contains duplicate version identities",
        )

    declared = (
        str(
            candidate.declared_predecessor_official_version_identifier
            or ""
        ).strip()
        or None
    )
    if not existing:
        if declared is not None:
            raise EffectiveTimeConflict(
                "predecessor_not_found",
                "an initial legal version cannot declare a predecessor",
            )
        return EffectiveLineageDecision(
            predecessor_version_id=None,
            lineage_type="initial",
            continuity_status=(
                "not_applicable"
                if candidate.continuity_policy == "not_applicable"
                else "initial"
            ),
        )

    latest = existing[-1]
    if (
        candidate.official_version_identifier
        == latest.official_version_identifier
    ):
        if declared not in {
            None,
            latest.official_version_identifier,
        }:
            raise EffectiveTimeConflict(
                "predecessor_mismatch",
                "source correction declares a different predecessor",
            )
        if _window_signature(candidate) != _window_signature(latest):
            raise EffectiveTimeConflict(
                "correction_window_mismatch",
                "source correction cannot rewrite an official time window",
            )
        return EffectiveLineageDecision(
            predecessor_version_id=latest.legal_resource_version_id,
            lineage_type="source_correction",
            continuity_status="same_window_correction",
            predecessor_effective_to=_legal_end(latest),
            successor_effective_from=_legal_start(candidate),
        )

    if any(
        item.official_version_identifier
        == candidate.official_version_identifier
        for item in existing
    ):
        raise EffectiveTimeConflict(
            "non_tip_correction",
            "an older official version cannot be corrected after the chain advanced",
        )
    if declared is None:
        raise EffectiveTimeConflict(
            "missing_predecessor",
            "a new official version must declare its effective predecessor",
        )
    if declared != latest.official_version_identifier:
        raise EffectiveTimeConflict(
            "predecessor_mismatch",
            "declared predecessor is not the current effective-time tip",
        )

    if candidate.continuity_policy == "not_applicable":
        return EffectiveLineageDecision(
            predecessor_version_id=latest.legal_resource_version_id,
            lineage_type="effective_successor",
            continuity_status="not_applicable",
        )

    predecessor_start = _legal_start(latest)
    successor_start = _legal_start(candidate)
    predecessor_end = _legal_end(latest)
    if predecessor_start is None or successor_start is None:
        raise EffectiveTimeConflict(
            "indeterminate_effective_time",
            "strict effective-time lineage requires predecessor and successor starts",
        )
    if successor_start <= predecessor_start:
        raise EffectiveTimeConflict(
            "effective_overlap",
            "successor starts on or before its predecessor",
        )

    if predecessor_end is None:
        derived_end = successor_start - timedelta(days=1)
        if derived_end < predecessor_start:
            raise EffectiveTimeConflict(
                "effective_overlap",
                "open-ended predecessor cannot be closed before it starts",
            )
        return EffectiveLineageDecision(
            predecessor_version_id=latest.legal_resource_version_id,
            lineage_type="effective_successor",
            continuity_status="open_end_closed",
            successor_effective_from=successor_start,
            derived_predecessor_effective_to=derived_end,
        )

    if successor_start <= predecessor_end:
        raise EffectiveTimeConflict(
            "effective_overlap",
            "successor overlaps the inclusive predecessor window",
        )
    expected_start = predecessor_end + timedelta(days=1)
    gap_days = (successor_start - expected_start).days
    if gap_days > 0:
        if candidate.continuity_policy != "allow_documented_gap":
            raise EffectiveTimeConflict(
                "effective_gap",
                f"effective-time chain contains an undocumented {gap_days}-day gap",
            )
        return EffectiveLineageDecision(
            predecessor_version_id=latest.legal_resource_version_id,
            lineage_type="effective_successor",
            continuity_status="documented_gap",
            predecessor_effective_to=predecessor_end,
            successor_effective_from=successor_start,
            gap_days=gap_days,
        )

    return EffectiveLineageDecision(
        predecessor_version_id=latest.legal_resource_version_id,
        lineage_type="effective_successor",
        continuity_status="contiguous",
        predecessor_effective_to=predecessor_end,
        successor_effective_from=successor_start,
    )


def _window_signature(
    value: EffectiveVersionWindow | EffectiveVersionCandidate,
) -> tuple[date | None, date | None, date | None, date | None]:
    return (
        value.effective_from,
        value.effective_to,
        value.valid_time_start,
        value.valid_time_end,
    )


def _legal_start(
    value: EffectiveVersionWindow | EffectiveVersionCandidate,
) -> date | None:
    return value.effective_from or value.valid_time_start


def _legal_end(
    value: EffectiveVersionWindow | EffectiveVersionCandidate,
) -> date | None:
    return value.effective_to or value.valid_time_end


def _ordered_range(
    start: date | None,
    end: date | None,
    name: str,
) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError(f"{name} end cannot precede start")


__all__ = [
    "EFFECTIVE_CONTINUITY_POLICIES",
    "EffectiveLineageDecision",
    "EffectiveTimeConflict",
    "EffectiveVersionCandidate",
    "EffectiveVersionWindow",
    "validate_effective_time_lineage",
]
