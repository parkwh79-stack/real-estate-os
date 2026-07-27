from __future__ import annotations

import tomllib
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SUPABASE_CONFIG = REPOSITORY_ROOT / "supabase" / "config.toml"


def _external_provider(name: str) -> dict[str, object]:
    with SUPABASE_CONFIG.open("rb") as config_file:
        config = tomllib.load(config_file)

    return config["auth"]["external"][name]


def test_google_oauth_uses_deployment_scoped_credentials() -> None:
    google = _external_provider("google")

    assert google["enabled"] is True
    assert google["client_id"] == (
        "env(SUPABASE_AUTH_EXTERNAL_GOOGLE_CLIENT_ID)"
    )
    assert google["secret"] == (
        "env(SUPABASE_AUTH_EXTERNAL_GOOGLE_CLIENT_SECRET)"
    )
    assert google["skip_nonce_check"] is False
    assert google["email_optional"] is False


def test_kakao_accepts_missing_email_after_provider_scope_succeeds() -> None:
    kakao = _external_provider("kakao")

    assert kakao["email_optional"] is True


def test_social_provider_secrets_are_never_literal_config_values() -> None:
    for provider_name in ("google", "kakao"):
        provider = _external_provider(provider_name)

        assert provider["enabled"] is True
        assert str(provider["client_id"]).startswith("env(")
        assert str(provider["secret"]).startswith("env(")
