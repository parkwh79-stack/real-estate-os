from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from agents.excel_workbook_learning_agent import profile_excel_workbook_for_learning


def learn_excel_workbook_mapping_use_case(
    workbook_path: str | Path,
    *,
    source_document: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Create a read-only workbook learning package for reviewer approval.

    This use case exists so API routes, MCP tools, batch jobs, and future UI
    workflows can reuse the same learning behavior without directly coupling to
    the agent implementation.
    """
    return profile_excel_workbook_for_learning(workbook_path, source_document=source_document)
