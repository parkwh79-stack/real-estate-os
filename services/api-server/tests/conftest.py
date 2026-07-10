import pytest


@pytest.fixture(autouse=True)
def _force_offline_market_comparables(monkeypatch):
    """Keep live 국토부 실거래가 injection OFF during the whole test suite.

    The deployment ``.env`` may enable ``REALYTICS_LIVE_MARKET_COMPARABLES`` so the
    running server auto-fetches real transactions in the classic PF-review run.
    ``main`` loads ``.env`` on import, so without this guard pytest would inherit
    the flag and the orchestration tests would make live network calls — breaking
    the "tests stay offline and deterministic" contract. Tests that exercise the
    enabled path pass an explicit ``environ`` dict to ``live_market_comparables_enabled``.
    """
    monkeypatch.delenv("REALYTICS_LIVE_MARKET_COMPARABLES", raising=False)
