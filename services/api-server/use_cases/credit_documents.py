from __future__ import annotations

from typing import Any, Dict

from agents.credit_document_agent import CreditDocumentAgent


def generate_credit_documents(project_data: Dict[str, Any], artifacts: Dict[str, Any]) -> Dict[str, Any]:
    """Build the Term Sheet and IC Memo from a PF review run's inputs/artifacts."""
    agent = CreditDocumentAgent()
    return {
        "termSheet": agent.build_term_sheet(project_data, artifacts),
        "icMemo": agent.build_ic_memo(project_data, artifacts),
    }
