"""Read-only operational status for the isolated legal-corpus worker."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.authz import authorize_request
from use_cases.legal_corpus_registry import (
    build_default_official_corpus_source_registry,
)
from use_cases.legal_corpus_worker import (
    inspect_legal_corpus_runtime_configuration,
)


router = APIRouter(
    prefix="/ops/legal-corpus",
    tags=["legal-corpus-operations"],
)

_OPERATOR_ROLES = frozenset({"admin", "compliance_officer"})


@router.get("/runtime")
def get_legal_corpus_runtime_status(
    request: Request,
) -> dict[str, Any]:
    """Expose sanitized capability/configuration state, never credentials."""

    authorization = authorize_request(request, "legal:operate")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required"},
        )
    user = authorization.get("user") or {}
    role = str(
        authorization.get("role")
        or user.get("role")
        or ""
    ).strip().casefold()
    if role not in _OPERATOR_ROLES:
        raise HTTPException(
            status_code=403,
            detail={"error": "legal_provider_operator_required"},
        )
    return inspect_legal_corpus_runtime_configuration(
        environment=os.environ,
        registry=build_default_official_corpus_source_registry(),
    )


__all__ = ["router"]
