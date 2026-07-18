import pytest


@pytest.fixture(autouse=True)
def _force_offline_external_connectors(monkeypatch):
    """Keep live market and legal connectors OFF during contract tests.

    The deployment ``.env`` may enable ``REALYTICS_LIVE_MARKET_COMPARABLES`` so the
    running server auto-fetches real transactions in the classic PF-review run.
    ``main`` loads ``.env`` on import, so without this guard pytest would inherit
    the flag and the orchestration tests would make live network calls — breaking
    the "tests stay offline and deterministic" contract. Tests that exercise the
    enabled path pass an explicit ``environ`` dict to the connector under test.
    """
    monkeypatch.delenv("REALYTICS_LIVE_MARKET_COMPARABLES", raising=False)
    monkeypatch.delenv("REALYTICS_KOREAN_LAW_MCP_URL", raising=False)
    monkeypatch.delenv("REALYTICS_LAW_OC", raising=False)
    monkeypatch.delenv("LAW_OC", raising=False)
    # ``main`` loads the developer .env during test collection.  Provider keys
    # from that file must not make contract tests call a cloud model or silently
    # route an explicitly unavailable provider to a different configured one.
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "TOGETHER_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    # Deployment OIDC/RBAC settings are also loaded from the developer .env at
    # import time. Contract tests opt into those controls explicitly, so clear
    # the live Supabase configuration to prevent cross-test issuer/audience and
    # strict-tenant leakage.
    for key in (
        "REALYTICS_IDP_PROVIDER",
        "REALYTICS_IDP_ISSUER",
        "REALYTICS_OIDC_ISSUER",
        "REALYTICS_IDP_AUDIENCE",
        "REALYTICS_OIDC_AUDIENCE",
        "REALYTICS_IDP_JWKS_URL",
        "REALYTICS_OIDC_JWKS_URL",
        "REALYTICS_IDP_PERSONAL_TENANT_FALLBACK",
        "REALYTICS_IDP_PERSONAL_TENANT_ROLE",
        "REALYTICS_RBAC_MODE",
        "REALYTICS_TENANT_ISOLATION_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("REALYTICS_IDP_MODE", "off")
