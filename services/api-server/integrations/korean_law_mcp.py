"""Deprecated compatibility facade for Korean legal retrieval.

MCP is an optional transport inside the Legal Intelligence Core.  This module
remains only for callers that have not migrated to the provider-independent
use case; it no longer implements a bridge-first source of truth.
"""

from __future__ import annotations

import os
from typing import Any, Dict


def run_korean_law_mcp(
    project_data: Dict[str, Any],
    *,
    timeout: float = 10.0,
    environ: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """Return the trusted compatibility projection from Legal Intelligence.

    ``timeout`` is retained for API compatibility. Provider retry/timeout
    policy is owned by the core adapter rather than by this facade.
    """

    del timeout
    # Function-local imports avoid a cycle while legacy modules are being
    # phased out.
    from use_cases.legal_intelligence import (
        build_legal_intelligence_service,
        resolve_project_legal_context,
    )

    service = build_legal_intelligence_service(
        environ=environ or os.environ,
    )
    resolved = resolve_project_legal_context(
        project_data,
        service=service,
    )
    context = dict(resolved["lawContext"])
    context.update(
        {
            "version": "realytics-korean-law-compatibility-v2",
            "transport": "provider_orchestration",
            "legalIntelligence": resolved["legalIntelligence"],
            "deprecated": True,
        }
    )
    return context


__all__ = ["run_korean_law_mcp"]
