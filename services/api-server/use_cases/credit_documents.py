from __future__ import annotations

from typing import Any, Dict

from agents.credit_document_agent import CreditDocumentAgent
from use_cases.covenant_eod import evaluate_covenant_eod_use_case


def _merge_covenant_overrides(project_data: Dict[str, Any], term_sheet_covenants: Dict[str, Any]) -> Dict[str, Any]:
    """Fill term-sheet covenant overrides from the derived engine, but never clobber
    an explicit reviewer/monitoring override already on ``project_data['covenants']``."""
    existing = project_data.get("covenants")
    existing = existing if isinstance(existing, dict) else {}
    # Derived values fill the gaps; anything the reviewer set explicitly wins.
    merged = {**(term_sheet_covenants or {}), **existing}
    return {**project_data, "covenants": merged}


def generate_credit_documents(project_data: Dict[str, Any], artifacts: Dict[str, Any]) -> Dict[str, Any]:
    """Build the Term Sheet and IC Memo from a PF review run's inputs/artifacts.

    The deterministic Covenant/EOD engine derives covenant thresholds + EOD
    conditions from the deal's numbers and feeds them into the Term Sheet so its
    covenant/EOD/discount/execution-order clauses carry real values instead of
    boilerplate placeholders. Explicit reviewer overrides on
    ``project_data['covenants']`` still take precedence.
    """
    agent = CreditDocumentAgent()
    covenant_eod: Dict[str, Any] = {}
    ts_project = project_data
    try:
        covenant_eod = evaluate_covenant_eod_use_case(project_data)
        ts_project = _merge_covenant_overrides(project_data, covenant_eod.get("termSheetCovenants") or {})
    except Exception:  # covenant derivation must never break credit-document generation
        covenant_eod = {}
        ts_project = project_data
    return {
        "termSheet": agent.build_term_sheet(ts_project, artifacts),
        "icMemo": agent.build_ic_memo(ts_project, artifacts),
        "covenantEod": covenant_eod,
    }
