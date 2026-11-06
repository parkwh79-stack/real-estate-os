from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from legal_intelligence.law_go_kr_contracts import (
    LAW_GO_KR_BOUNDARY_CONTRACTS,
    LawGoKrExternalContractError,
    LawGoKrExternalListPage,
    LawGoKrExternalPayloadAdapter,
)
from legal_intelligence.models import LegalResourceKind


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "law_go_kr"
    / "official_contract_samples.json"
)
SAMPLES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

EXPECTED_IDENTITIES = {
    LegalResourceKind.STATUTE: "001234",
    LegalResourceKind.PROVISION: "001234",
    LegalResourceKind.PRECEDENT: "P-2026-001",
    LegalResourceKind.INTERPRETATION: "E-2026-001",
    LegalResourceKind.ADMINISTRATIVE_RULE: "A-001",
    LegalResourceKind.LOCAL_ORDINANCE: "DGU-001",
    LegalResourceKind.CONSTITUTIONAL_DECISION: "C-2026-001",
    LegalResourceKind.LEGAL_TERM: "T-001",
}


@pytest.mark.parametrize("resource_kind", tuple(EXPECTED_IDENTITIES))
def test_official_fixture_satisfies_target_list_detail_history_contracts(
    resource_kind: LegalResourceKind,
):
    fixture = SAMPLES[resource_kind.value]
    expected_identifier = EXPECTED_IDENTITIES[resource_kind]
    adapter = LawGoKrExternalPayloadAdapter(resource_kind)

    list_page = adapter.parse_list(
        fixture["list"],
        requested_page=1,
        requested_display=1,
    )
    detail = adapter.parse_detail(
        fixture["detail"],
        requested_official_identifier=expected_identifier,
    )
    history = adapter.parse_history(
        fixture["history"],
        requested_official_identifier=expected_identifier,
    )

    assert list_page.resource_kind == resource_kind
    assert list_page.target == adapter.contract.list_target
    assert list_page.total_count == 1
    assert list_page.rows[0].official_identifier == expected_identifier
    assert list_page.raw_payload == fixture["list"]
    assert detail.response_official_identifier == expected_identifier
    assert detail.raw_payload == fixture["detail"]
    assert history.versions
    assert history.raw_payload == fixture["history"]
    assert all(
        version.official_version_identifier
        for version in history.versions
    )


def test_registry_has_contract_for_every_supported_non_committee_target():
    expected = set(LegalResourceKind) - {
        LegalResourceKind.COMMITTEE_DECISION
    }

    assert set(LAW_GO_KR_BOUNDARY_CONTRACTS) == expected
    assert set(SAMPLES) == {kind.value for kind in expected}


@pytest.mark.parametrize("resource_kind", tuple(EXPECTED_IDENTITIES))
def test_detail_contract_fails_closed_on_cross_record_identity(
    resource_kind: LegalResourceKind,
):
    adapter = LawGoKrExternalPayloadAdapter(resource_kind)

    with pytest.raises(
        LawGoKrExternalContractError,
        match="does not match",
    ):
        adapter.parse_detail(
            SAMPLES[resource_kind.value]["detail"],
            requested_official_identifier="different-record",
        )


def test_list_contract_rejects_unregistered_envelope_and_missing_total():
    adapter = LawGoKrExternalPayloadAdapter(LegalResourceKind.STATUTE)

    with pytest.raises(
        LawGoKrExternalContractError,
        match="registered envelope",
    ):
        adapter.parse_list(
            {"Unexpected": {"law": []}},
            requested_page=1,
            requested_display=1,
        )

    with pytest.raises(
        LawGoKrExternalContractError,
        match="total count",
    ):
        adapter.parse_list(
            {
                "LawSearch": {
                    "page": "1",
                    "numOfRows": "1",
                    "law": [],
                }
            },
            requested_page=1,
            requested_display=1,
        )


def test_list_contract_checks_page_and_display_echoes():
    adapter = LawGoKrExternalPayloadAdapter(LegalResourceKind.STATUTE)
    fixture = SAMPLES["statute"]["list"]

    with pytest.raises(
        LawGoKrExternalContractError,
        match="page does not match",
    ):
        adapter.parse_list(
            fixture,
            requested_page=2,
            requested_display=1,
        )

    with pytest.raises(
        LawGoKrExternalContractError,
        match="display does not match",
    ):
        adapter.parse_list(
            fixture,
            requested_page=1,
            requested_display=20,
        )


def test_history_contract_rejects_unversioned_rows():
    adapter = LawGoKrExternalPayloadAdapter(LegalResourceKind.STATUTE)

    with pytest.raises(
        LawGoKrExternalContractError,
        match="version identity",
    ):
        adapter.parse_history(
            {
                "LawHistory": {
                    "법령ID": "001234",
                    "lawHistory": [{"시행일자": "20260701"}]
                }
            },
            requested_official_identifier="001234",
        )


def test_history_contract_fails_closed_on_cross_record_identity():
    adapter = LawGoKrExternalPayloadAdapter(LegalResourceKind.STATUTE)

    with pytest.raises(
        LawGoKrExternalContractError,
        match="history identity does not match",
    ):
        adapter.parse_history(
            SAMPLES["statute"]["history"],
            requested_official_identifier="different-record",
        )


def test_normalized_boundary_models_forbid_unreviewed_fields():
    adapter = LawGoKrExternalPayloadAdapter(LegalResourceKind.STATUTE)
    page = adapter.parse_list(
        SAMPLES["statute"]["list"],
        requested_page=1,
        requested_display=1,
    )

    with pytest.raises(ValidationError):
        LawGoKrExternalListPage.model_validate(
            {
                **page.model_dump(),
                "callerConnected": True,
            }
        )
