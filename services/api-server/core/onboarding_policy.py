"""Server-owned onboarding consent policy versions.

Clients render these identifiers, but they never choose which legal text is
current.  The FastAPI boundary compares every submitted version with this
server configuration before PostgreSQL records acceptance.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


TERMS_VERSION_ENV = "REALYTICS_TERMS_VERSION"
PRIVACY_VERSION_ENV = "REALYTICS_PRIVACY_VERSION"
DEFAULT_TERMS_VERSION = "realytics-terms-2026-07-30"
DEFAULT_PRIVACY_VERSION = "realytics-privacy-2026-07-30"


class OnboardingPolicyVersionError(ValueError):
    """Stable policy-version mismatch without echoing untrusted input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def current_onboarding_policy_versions(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    env = environ if environ is not None else os.environ
    terms_version = str(
        env.get(TERMS_VERSION_ENV) or DEFAULT_TERMS_VERSION
    ).strip()
    privacy_version = str(
        env.get(PRIVACY_VERSION_ENV) or DEFAULT_PRIVACY_VERSION
    ).strip()
    if not terms_version or not privacy_version:
        raise RuntimeError("onboarding policy versions are not configured")
    return terms_version, privacy_version


def require_current_onboarding_policy_versions(
    *,
    terms_version: str,
    privacy_version: str,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    current_terms, current_privacy = current_onboarding_policy_versions(
        environ
    )
    if str(terms_version or "").strip() != current_terms:
        raise OnboardingPolicyVersionError("terms_version_mismatch")
    if str(privacy_version or "").strip() != current_privacy:
        raise OnboardingPolicyVersionError("privacy_version_mismatch")
    return current_terms, current_privacy


__all__ = [
    "DEFAULT_PRIVACY_VERSION",
    "DEFAULT_TERMS_VERSION",
    "OnboardingPolicyVersionError",
    "PRIVACY_VERSION_ENV",
    "TERMS_VERSION_ENV",
    "current_onboarding_policy_versions",
    "require_current_onboarding_policy_versions",
]
