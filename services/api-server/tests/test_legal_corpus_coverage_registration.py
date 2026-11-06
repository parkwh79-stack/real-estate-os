from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)
from legal_intelligence.law_go_kr_contracts import (
    LAW_GO_KR_BOUNDARY_CONTRACTS,
)
from use_cases.legal_corpus_coverage_registration import (
    COVERAGE_REGISTRATION_MANIFEST_VERSION,
    LegalCorpusCoverageRegistrationError,
    LegalCorpusCoverageRegistrationResult,
    LegalCorpusCoverageRegistrationService,
    build_coverage_contract_set_plan,
    load_coverage_contract_set_manifest,
)


def coverage_identities(
    *,
    registry_version: str = "official-corpus-registry-v2",
) -> tuple[OfficialCorpusCoverageIdentity, ...]:
    return tuple(
        OfficialCorpusCoverageIdentity(
            provider_key="law-go-kr",
            provider_revision=2,
            registry_version=registry_version,
            resource_kind=kind.value,
            target=contract.list_target,
            query_sha256=hashlib.sha256(
                f"reviewed-full-query:{kind.value}".encode("utf-8")
            ).hexdigest(),
            source_adapter_version="law-go-kr-approved-adapter-v2",
            boundary_contract_version=(
                f"{contract.list_contract_version}."
                f"{contract.detail_contract_version}."
                f"{contract.history_contract_version}"
            ),
            coverage_contract_version="law-go-kr-full-corpus-v1",
        )
        for kind, contract in sorted(
            LAW_GO_KR_BOUNDARY_CONTRACTS.items(),
            key=lambda item: item[0].value,
        )
    )


def test_plan_requires_and_hashes_exactly_eight_code_owned_targets():
    identities = coverage_identities()

    plan = build_coverage_contract_set_plan(
        tuple(reversed(identities)),
        registered_by="deployment:release-2026-07-20",
    )

    expected_hash = hashlib.sha256(
        "\n".join(
            sorted(
                identity.coverage_contract_id
                for identity in identities
            )
        ).encode("utf-8")
    ).hexdigest()
    assert len(plan.identities) == 8
    assert plan.expected_contracts_sha256 == expected_hash
    assert plan.coverage_registry_id == identities[0].coverage_registry_id
    assert plan.metadata["registrationPath"] == (
        "deployment-migration-role-v1"
    )


def test_plan_rejects_partial_duplicate_and_mixed_registry_sets():
    identities = coverage_identities()

    with pytest.raises(
        LegalCorpusCoverageRegistrationError,
        match="exactly 8",
    ):
        build_coverage_contract_set_plan(
            identities[:-1],
            registered_by="deployment:test",
        )

    with pytest.raises(
        LegalCorpusCoverageRegistrationError,
        match="duplicate",
    ):
        build_coverage_contract_set_plan(
            (*identities[:-1], identities[0]),
            registered_by="deployment:test",
        )

    mixed = (
        *identities[:-1],
        replace(
            identities[-1],
            registry_version="official-corpus-registry-v3",
        ),
    )
    with pytest.raises(
        LegalCorpusCoverageRegistrationError,
        match="all coverage identities",
    ):
        build_coverage_contract_set_plan(
            mixed,
            registered_by="deployment:test",
        )


def test_plan_rejects_target_alias_that_is_not_the_boundary_contract():
    identities = coverage_identities()
    aliased = (
        replace(identities[0], target="lawAlias"),
        *identities[1:],
    )

    with pytest.raises(
        LegalCorpusCoverageRegistrationError,
        match="code-owned required targets",
    ):
        build_coverage_contract_set_plan(
            aliased,
            registered_by="deployment:test",
        )


def test_plan_rejects_unreviewed_boundary_contract_version():
    identities = coverage_identities()
    unreviewed = (
        replace(
            identities[0],
            boundary_contract_version="unreviewed-boundary-v1",
        ),
        *identities[1:],
    )

    with pytest.raises(
        LegalCorpusCoverageRegistrationError,
        match="code-owned boundary",
    ):
        build_coverage_contract_set_plan(
            unreviewed,
            registered_by="deployment:test",
        )


def test_manifest_loader_validates_supplied_derived_hashes(tmp_path):
    identities = coverage_identities()
    rows = [dict(identity.as_dict()) for identity in identities]
    manifest = tmp_path / "coverage.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": (
                    COVERAGE_REGISTRATION_MANIFEST_VERSION
                ),
                "registeredBy": "deployment:test",
                "identities": rows,
            }
        ),
        encoding="utf-8",
    )

    plan = load_coverage_contract_set_manifest(manifest)

    assert len(plan.identities) == 8
    assert plan.provider_id == identities[0].provider_id

    rows[0]["coverageIdentitySha256"] = "0" * 64
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": (
                    COVERAGE_REGISTRATION_MANIFEST_VERSION
                ),
                "registeredBy": "deployment:test",
                "identities": rows,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        LegalCorpusCoverageRegistrationError,
        match="mismatched coverageIdentitySha256",
    ):
        load_coverage_contract_set_manifest(manifest)


def test_manifest_loader_rejects_duplicate_json_keys(tmp_path):
    manifest = tmp_path / "duplicate.json"
    manifest.write_text(
        (
            '{"schemaVersion":"'
            + COVERAGE_REGISTRATION_MANIFEST_VERSION
            + '","registeredBy":"deployment:first",'
            '"registeredBy":"deployment:second","identities":[]}'
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        LegalCorpusCoverageRegistrationError,
        match="repeats JSON key",
    ):
        load_coverage_contract_set_manifest(manifest)


def test_service_keeps_inspection_and_registration_explicit():
    plan = build_coverage_contract_set_plan(
        coverage_identities(),
        registered_by="deployment:test",
    )

    class Repository:
        def __init__(self):
            self.calls = []

        def inspect(self, value):
            self.calls.append(("inspect", value))
            return LegalCorpusCoverageRegistrationResult(
                action="would_register",
                coverage_registry_id=value.coverage_registry_id,
                expected_contract_count=8,
                expected_contracts_sha256=(
                    value.expected_contracts_sha256
                ),
                provider_id=value.provider_id,
                provider_registered=False,
            )

        def register(self, value):
            self.calls.append(("register", value))
            return LegalCorpusCoverageRegistrationResult(
                action="registered",
                coverage_registry_id=value.coverage_registry_id,
                expected_contract_count=8,
                expected_contracts_sha256=(
                    value.expected_contracts_sha256
                ),
                provider_id=value.provider_id,
                provider_registered=False,
            )

    repository = Repository()
    service = LegalCorpusCoverageRegistrationService(repository)

    assert service.inspect(plan).action == "would_register"
    assert service.register(plan).action == "registered"
    assert repository.calls == [("inspect", plan), ("register", plan)]
