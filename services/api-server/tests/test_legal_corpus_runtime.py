from __future__ import annotations

import pytest

from use_cases.legal_corpus_runtime import (
    build_legal_corpus_batch_coordinator,
)


class FakeImmutableStore:
    def __init__(self):
        self.verified = False

    def verify_configuration(self):
        self.verified = True


def test_runtime_factory_verifies_immutable_storage_and_wires_one_role():
    store = FakeImmutableStore()
    connection_factory = lambda: object()

    coordinator = build_legal_corpus_batch_coordinator(
        connection_factory=connection_factory,
        raw_object_store=store,
        max_pages=7,
    )

    assert store.verified is True
    assert coordinator._max_pages == 7
    assert (
        coordinator._sync_service._repository._connect
        is connection_factory
    )
    assert (
        coordinator._completion_repository._connect
        is connection_factory
    )


def test_runtime_factory_can_defer_storage_probe_for_contract_tests():
    store = FakeImmutableStore()

    build_legal_corpus_batch_coordinator(
        connection_factory=lambda: object(),
        raw_object_store=store,
        verify_storage=False,
    )

    assert store.verified is False


@pytest.mark.parametrize("value", ["0", "-1", "not-an-int"])
def test_runtime_factory_rejects_invalid_page_limit(monkeypatch, value):
    monkeypatch.setenv("REALYTICS_LEGAL_SYNC_MAX_PAGES", value)

    with pytest.raises(RuntimeError, match="MAX_PAGES"):
        build_legal_corpus_batch_coordinator(
            connection_factory=lambda: object(),
            raw_object_store=FakeImmutableStore(),
            verify_storage=False,
        )
