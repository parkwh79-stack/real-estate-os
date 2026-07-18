from __future__ import annotations

"""Fail-closed completeness policies for approved Parser V2 handoffs.

Domain prefixes belong to input-scope discovery in ``domain_standards``.  They
must never prove that an approved snapshot is complete.  Completeness is based
only on exact canonical fields and, where applicable, exact field sets carried
by the same entity.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from parsing_platform.canonical_fields import canonical_field_key


DOMAIN_HANDOFF_POLICY_VERSION = "realytics-domain-handoff-completeness-v2"
APPROVED_POLICY_STATUS = "approved"
NEEDS_DOMAIN_APPROVAL_STATUS = "needs_domain_approval"
ENTITY_CARDINALITY_ALL_EXPECTED = "all_expected"
ENTITY_CARDINALITY_AT_LEAST = "at_least"
ENTITY_CARDINALITIES = frozenset(
    {
        ENTITY_CARDINALITY_ALL_EXPECTED,
        ENTITY_CARDINALITY_AT_LEAST,
    }
)


def _canonical_exact_fields(
    values: Iterable[str],
    *,
    label: str,
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        raw = str(raw_value or "").strip()
        if not raw:
            raise ValueError(f"{label} cannot contain an empty field")
        if raw.endswith(".") or "*" in raw:
            raise ValueError(
                f"{label} accepts exact canonical fields only, not prefixes or wildcards: "
                f"{raw}"
            )
        field_key = canonical_field_key(raw)
        if field_key not in seen:
            seen.add(field_key)
            normalized.append(field_key)
    return tuple(normalized)


@dataclass(frozen=True)
class EntityScopedRequiredFieldGroup:
    """Exact fields that must coexist on each covered entity."""

    group_id: str
    required_fields: tuple[str, ...]
    cardinality: str = ENTITY_CARDINALITY_ALL_EXPECTED
    minimum_entity_count: int = 1
    expected_entity_ids_metadata_key: str = "expectedEntityIds"
    expected_entity_count_metadata_key: str = "expectedEntityCount"

    def __post_init__(self) -> None:
        group_id = str(self.group_id or "").strip()
        if not group_id:
            raise ValueError("entity-scoped requirement group_id is required")
        object.__setattr__(self, "group_id", group_id)
        fields = _canonical_exact_fields(
            self.required_fields,
            label=f"entity group {group_id}",
        )
        if not fields:
            raise ValueError(
                f"entity group {group_id} must require at least one exact field"
            )
        object.__setattr__(self, "required_fields", fields)
        cardinality = str(self.cardinality or "").strip()
        if cardinality not in ENTITY_CARDINALITIES:
            raise ValueError(
                "entity group cardinality must be one of: "
                + ", ".join(sorted(ENTITY_CARDINALITIES))
            )
        object.__setattr__(self, "cardinality", cardinality)
        if (
            isinstance(self.minimum_entity_count, bool)
            or not isinstance(self.minimum_entity_count, int)
            or self.minimum_entity_count < 1
        ):
            raise ValueError("minimum_entity_count must be a positive integer")
        for name in (
            "expected_entity_ids_metadata_key",
            "expected_entity_count_metadata_key",
        ):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "groupId": self.group_id,
            "requiredFields": list(self.required_fields),
            "cardinality": self.cardinality,
            "minimumEntityCount": self.minimum_entity_count,
            "expectedEntityIdsMetadataKey": self.expected_entity_ids_metadata_key,
            "expectedEntityCountMetadataKey": self.expected_entity_count_metadata_key,
        }


@dataclass(frozen=True)
class DomainHandoffPolicy:
    domain: str
    required_approved_fields: tuple[str, ...] = ()
    entity_scoped_required_field_groups: tuple[
        EntityScopedRequiredFieldGroup, ...
    ] = ()
    status: str = APPROVED_POLICY_STATUS
    version: str = DOMAIN_HANDOFF_POLICY_VERSION

    def __post_init__(self) -> None:
        domain = str(self.domain or "").strip()
        if not domain:
            raise ValueError("handoff policy domain is required")
        object.__setattr__(self, "domain", domain)
        object.__setattr__(
            self,
            "required_approved_fields",
            _canonical_exact_fields(
                self.required_approved_fields,
                label=f"{domain} required_approved_fields",
            ),
        )
        groups = tuple(self.entity_scoped_required_field_groups)
        if len({item.group_id for item in groups}) != len(groups):
            raise ValueError(
                f"{domain} entity-scoped requirement group IDs must be unique"
            )
        object.__setattr__(
            self,
            "entity_scoped_required_field_groups",
            groups,
        )
        if (
            self.status == APPROVED_POLICY_STATUS
            and not self.required_approved_fields
            and not groups
        ):
            raise ValueError(
                f"approved handoff policy {domain} must declare exact completeness requirements"
            )


@dataclass(frozen=True)
class EntityScopedCoverage:
    group_id: str
    required_fields: tuple[str, ...]
    cardinality: str
    minimum_entity_count: int
    expected_entity_ids: tuple[str, ...]
    expected_entity_count: int | None
    complete_entity_ids: tuple[str, ...]
    missing_expected_entity_ids: tuple[str, ...]
    missing_fields_by_entity: tuple[tuple[str, tuple[str, ...]], ...]
    metadata_issues: tuple[str, ...]
    complete: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "groupId": self.group_id,
            "requiredFields": list(self.required_fields),
            "cardinality": self.cardinality,
            "minimumEntityCount": self.minimum_entity_count,
            "expectedEntityIds": list(self.expected_entity_ids),
            "expectedEntityCount": self.expected_entity_count,
            "completeEntityIds": list(self.complete_entity_ids),
            "completeEntityCount": len(self.complete_entity_ids),
            "missingExpectedEntityIds": list(self.missing_expected_entity_ids),
            "missingFieldsByEntity": {
                entity_id: list(fields)
                for entity_id, fields in self.missing_fields_by_entity
            },
            "metadataIssues": list(self.metadata_issues),
            "complete": self.complete,
        }


@dataclass(frozen=True)
class HandoffCompletenessEvaluation:
    missing_required_fields: tuple[str, ...]
    entity_scoped_coverage: tuple[EntityScopedCoverage, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_required_fields and all(
            item.complete for item in self.entity_scoped_coverage
        )


def _draft_policy(domain: str) -> DomainHandoffPolicy:
    # A prefix-only policy cannot be approved.  The domain remains available
    # for scoped review, while authoritative execution stays fail-closed until
    # domain owners approve an exact field/cardinality contract.
    return DomainHandoffPolicy(
        domain=domain,
        status=NEEDS_DOMAIN_APPROVAL_STATUS,
    )


_POLICIES: tuple[DomainHandoffPolicy, ...] = (
    _draft_policy("project_identity"),
    DomainHandoffPolicy(
        domain="land_legal",
        entity_scoped_required_field_groups=(
            EntityScopedRequiredFieldGroup(
                group_id="parcel_core",
                required_fields=(
                    "land.pnu",
                    "land.registered_area_sqm",
                    "land.zoning.primary_use_district_code",
                ),
                cardinality=ENTITY_CARDINALITY_ALL_EXPECTED,
                minimum_entity_count=1,
            ),
        ),
    ),
    _draft_policy("permit_planning"),
    _draft_policy("building_product"),
    _draft_policy("market_product"),
    DomainHandoffPolicy(
        domain="development_feasibility",
        required_approved_fields=(
            "program.gross_floor_area_sqm",
            "revenue.total_sales_revenue",
            "cost.total_project_cost",
        ),
    ),
    _draft_policy("cost_schedule"),
    DomainHandoffPolicy(
        domain="pf_credit",
        required_approved_fields=("financing.pf_amount",),
    ),
    _draft_policy("security_collateral"),
    _draft_policy("contract_obligation"),
    _draft_policy("lease_operations"),
    _draft_policy("valuation_exit"),
    _draft_policy("cashflow_waterfall"),
    _draft_policy("risk_approval"),
)

DOMAIN_HANDOFF_POLICIES = {item.domain: item for item in _POLICIES}


def get_domain_handoff_policy(domain: str) -> DomainHandoffPolicy:
    try:
        return DOMAIN_HANDOFF_POLICIES[str(domain)]
    except KeyError as exc:
        raise ValueError(
            f"no governed handoff completeness policy for domain: {domain}"
        ) from exc


def evaluate_handoff_completeness(
    policy: DomainHandoffPolicy,
    approved_inputs: Iterable[Mapping[str, Any]],
    *,
    snapshot_metadata: Mapping[str, Any] | None = None,
) -> HandoffCompletenessEvaluation:
    canonical_inputs: list[tuple[str, str | None]] = []
    for item in approved_inputs:
        try:
            field_key = canonical_field_key(item.get("fieldKey"))
        except ValueError:
            continue
        raw_entity_id = item.get("entityId")
        entity_id = (
            str(raw_entity_id).strip()
            if raw_entity_id is not None
            else None
        )
        canonical_inputs.append((field_key, entity_id or None))

    approved_fields = {field_key for field_key, _ in canonical_inputs}
    missing_fields = tuple(
        field_key
        for field_key in policy.required_approved_fields
        if field_key not in approved_fields
    )
    metadata = (
        snapshot_metadata
        if isinstance(snapshot_metadata, Mapping)
        else {}
    )
    coverage = tuple(
        _evaluate_entity_group(
            group,
            canonical_inputs,
            metadata=metadata,
        )
        for group in policy.entity_scoped_required_field_groups
    )
    return HandoffCompletenessEvaluation(
        missing_required_fields=missing_fields,
        entity_scoped_coverage=coverage,
    )


def _evaluate_entity_group(
    group: EntityScopedRequiredFieldGroup,
    approved_inputs: Sequence[tuple[str, str | None]],
    *,
    metadata: Mapping[str, Any],
) -> EntityScopedCoverage:
    fields_by_entity: dict[str, set[str]] = {}
    for field_key, entity_id in approved_inputs:
        if entity_id is None:
            continue
        fields_by_entity.setdefault(entity_id, set()).add(field_key)

    expected_ids, expected_count, metadata_issues = _expected_entities(
        group,
        metadata,
    )
    required = set(group.required_fields)
    all_complete_ids = {
        entity_id
        for entity_id, fields in fields_by_entity.items()
        if required.issubset(fields)
    }

    if expected_ids:
        complete_ids = tuple(
            entity_id
            for entity_id in expected_ids
            if entity_id in all_complete_ids
        )
        missing_expected_ids = tuple(
            entity_id
            for entity_id in expected_ids
            if entity_id not in all_complete_ids
        )
        missing_by_entity = tuple(
            (
                entity_id,
                tuple(
                    field_key
                    for field_key in group.required_fields
                    if field_key
                    not in fields_by_entity.get(entity_id, set())
                ),
            )
            for entity_id in missing_expected_ids
        )
    else:
        complete_ids = tuple(sorted(all_complete_ids))
        missing_expected_ids = ()
        missing_by_entity = tuple(
            (
                entity_id,
                tuple(
                    field_key
                    for field_key in group.required_fields
                    if field_key not in fields
                ),
            )
            for entity_id, fields in sorted(fields_by_entity.items())
            if not required.issubset(fields)
        )

    required_count = max(
        group.minimum_entity_count,
        expected_count or 0,
        len(expected_ids),
    )
    cardinality_complete = len(complete_ids) >= required_count
    if group.cardinality == ENTITY_CARDINALITY_ALL_EXPECTED:
        cardinality_complete = cardinality_complete and not missing_expected_ids
    complete = not metadata_issues and cardinality_complete

    return EntityScopedCoverage(
        group_id=group.group_id,
        required_fields=group.required_fields,
        cardinality=group.cardinality,
        minimum_entity_count=group.minimum_entity_count,
        expected_entity_ids=expected_ids,
        expected_entity_count=expected_count,
        complete_entity_ids=complete_ids,
        missing_expected_entity_ids=missing_expected_ids,
        missing_fields_by_entity=missing_by_entity,
        metadata_issues=metadata_issues,
        complete=complete,
    )


def _expected_entities(
    group: EntityScopedRequiredFieldGroup,
    metadata: Mapping[str, Any],
) -> tuple[tuple[str, ...], int | None, tuple[str, ...]]:
    issues: list[str] = []
    raw_ids = metadata.get(group.expected_entity_ids_metadata_key)
    expected_ids: tuple[str, ...] = ()
    if raw_ids is not None:
        if (
            not isinstance(raw_ids, Sequence)
            or isinstance(raw_ids, (str, bytes, bytearray))
        ):
            issues.append("EXPECTED_ENTITY_IDS_INVALID")
        else:
            normalized_ids = tuple(str(item or "").strip() for item in raw_ids)
            if any(not item for item in normalized_ids):
                issues.append("EXPECTED_ENTITY_IDS_INVALID")
            elif len(set(normalized_ids)) != len(normalized_ids):
                issues.append("EXPECTED_ENTITY_IDS_DUPLICATED")
            else:
                expected_ids = normalized_ids

    raw_count = metadata.get(group.expected_entity_count_metadata_key)
    expected_count: int | None = None
    if raw_count is not None:
        if (
            isinstance(raw_count, bool)
            or not isinstance(raw_count, int)
            or raw_count < 0
        ):
            issues.append("EXPECTED_ENTITY_COUNT_INVALID")
        else:
            expected_count = raw_count

    if (
        expected_ids
        and expected_count is not None
        and expected_count != len(expected_ids)
    ):
        issues.append("EXPECTED_ENTITY_METADATA_COUNT_MISMATCH")

    return expected_ids, expected_count, tuple(issues)


def missing_required_field_groups(
    policy: DomainHandoffPolicy,
    field_keys: Iterable[str],
) -> tuple[tuple[str, ...], ...]:
    """Fail-closed compatibility projection for field-only legacy callers.

    A caller that supplies no ``entityId`` can never prove entity-scoped
    completeness, so every entity group remains missing.
    """

    inputs = ({"fieldKey": item, "entityId": None} for item in field_keys)
    evaluation = evaluate_handoff_completeness(policy, inputs)
    return (
        *(tuple((field_key,)) for field_key in evaluation.missing_required_fields),
        *(
            group.required_fields
            for group in policy.entity_scoped_required_field_groups
        ),
    )


__all__ = [
    "APPROVED_POLICY_STATUS",
    "DOMAIN_HANDOFF_POLICIES",
    "DOMAIN_HANDOFF_POLICY_VERSION",
    "ENTITY_CARDINALITIES",
    "ENTITY_CARDINALITY_ALL_EXPECTED",
    "ENTITY_CARDINALITY_AT_LEAST",
    "NEEDS_DOMAIN_APPROVAL_STATUS",
    "DomainHandoffPolicy",
    "EntityScopedCoverage",
    "EntityScopedRequiredFieldGroup",
    "HandoffCompletenessEvaluation",
    "evaluate_handoff_completeness",
    "get_domain_handoff_policy",
    "missing_required_field_groups",
]
