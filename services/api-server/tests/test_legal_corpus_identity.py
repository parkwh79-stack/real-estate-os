from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from legal_intelligence.corpus_identity import (
    OfficialResourceIdentity,
    legal_resource_crosswalk_id,
)
from legal_intelligence.effective_time import (
    EffectiveTimeConflict,
    EffectiveVersionCandidate,
    EffectiveVersionWindow,
    validate_effective_time_lineage,
)


def test_official_identity_excludes_provider_revision() -> None:
    identity = OfficialResourceIdentity(
        provider_key="law-go-kr",
        resource_type="statute",
        canonical_official_identifier="법률-00001",
        jurisdiction_code="KR",
    )

    assert "providerRevision" not in identity.payload
    assert identity.cross_revision_key.startswith("official-resource:v1:")
    assert identity.legal_resource_id.startswith("legal-resource:")

    revision_1 = legal_resource_crosswalk_id(
        provider_id="legal-provider:revision-1",
        provider_resource_type="statute",
        provider_official_identifier="법률-00001",
        provider_jurisdiction_code="KR",
    )
    revision_2 = legal_resource_crosswalk_id(
        provider_id="legal-provider:revision-2",
        provider_resource_type="statute",
        provider_official_identifier="법률 제00001호",
        provider_jurisdiction_code="KR",
    )
    assert revision_1 != revision_2


def _window(
    *,
    version_id: str = "version-1",
    official_version: str = "official-v1",
    sequence: int = 1,
    start: date = date(2026, 1, 1),
    end: date | None = date(2026, 1, 31),
) -> EffectiveVersionWindow:
    return EffectiveVersionWindow(
        legal_resource_version_id=version_id,
        official_version_identifier=official_version,
        version_sequence=sequence,
        effective_from=start,
        effective_to=end,
        valid_time_start=start,
        valid_time_end=end,
    )


def _candidate(
    *,
    version_id: str = "version-2",
    official_version: str = "official-v2",
    predecessor: str | None = "official-v1",
    start: date = date(2026, 2, 1),
    end: date | None = None,
    policy: str = "strict_contiguous",
    gap_reason: str | None = None,
) -> EffectiveVersionCandidate:
    return EffectiveVersionCandidate(
        legal_resource_version_id=version_id,
        official_version_identifier=official_version,
        effective_from=start,
        effective_to=end,
        valid_time_start=start,
        valid_time_end=end,
        declared_predecessor_official_version_identifier=predecessor,
        continuity_policy=policy,
        gap_reason=gap_reason,
    )


def test_effective_successor_requires_declared_current_predecessor() -> None:
    current = _window()

    with pytest.raises(EffectiveTimeConflict) as missing:
        validate_effective_time_lineage(
            (current,),
            _candidate(predecessor=None),
        )
    assert missing.value.code == "missing_predecessor"

    with pytest.raises(EffectiveTimeConflict) as mismatched:
        validate_effective_time_lineage(
            (current,),
            _candidate(predecessor="official-v0"),
        )
    assert mismatched.value.code == "predecessor_mismatch"


def test_effective_successor_rejects_overlap_and_undocumented_gap() -> None:
    current = _window()

    with pytest.raises(EffectiveTimeConflict) as overlap:
        validate_effective_time_lineage(
            (current,),
            _candidate(start=date(2026, 1, 31)),
        )
    assert overlap.value.code == "effective_overlap"

    with pytest.raises(EffectiveTimeConflict) as gap:
        validate_effective_time_lineage(
            (current,),
            _candidate(start=date(2026, 2, 3)),
        )
    assert gap.value.code == "effective_gap"


def test_effective_successor_accepts_contiguous_and_documented_gap() -> None:
    current = _window()

    contiguous = validate_effective_time_lineage(
        (current,),
        _candidate(),
    )
    assert contiguous.predecessor_version_id == "version-1"
    assert contiguous.continuity_status == "contiguous"
    assert contiguous.gap_days == 0

    documented = validate_effective_time_lineage(
        (current,),
        _candidate(
            start=date(2026, 2, 3),
            policy="allow_documented_gap",
            gap_reason="official source has no version effective for 2 days",
        ),
    )
    assert documented.continuity_status == "documented_gap"
    assert documented.gap_days == 2


def test_open_ended_predecessor_gets_immutable_derived_closure() -> None:
    current = _window(end=None)
    candidate = _candidate(start=date(2026, 3, 1))

    decision = validate_effective_time_lineage((current,), candidate)

    assert decision.continuity_status == "open_end_closed"
    assert decision.derived_predecessor_effective_to == date(2026, 2, 28)


def test_source_correction_must_keep_the_exact_official_window() -> None:
    current = _window()
    correction = _candidate(
        official_version="official-v1",
        predecessor=None,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
    )

    decision = validate_effective_time_lineage((current,), correction)
    assert decision.lineage_type == "source_correction"
    assert decision.continuity_status == "same_window_correction"

    with pytest.raises(EffectiveTimeConflict) as changed:
        validate_effect_time = validate_effective_time_lineage
        validate_effect_time(
            (current,),
            replace(
                correction,
                effective_to=date(2026, 2, 1),
                valid_time_end=date(2026, 2, 1),
            ),
        )
    assert changed.value.code == "correction_window_mismatch"


def test_initial_version_cannot_claim_a_nonexistent_predecessor() -> None:
    with pytest.raises(EffectiveTimeConflict) as conflict:
        validate_effective_time_lineage(
            (),
            _candidate(predecessor="official-v0"),
        )
    assert conflict.value.code == "predecessor_not_found"
