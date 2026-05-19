from __future__ import annotations

from typing import Any, Dict

from agents.land_ledger_agent import LandLedgerAgent


def analyze_land_ledger_workbook_use_case(
    file_path: str,
    import_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Analyze an uploaded land ledger workbook from a prepared local file path."""
    return LandLedgerAgent().analyze_xls(file_path, import_metadata)
