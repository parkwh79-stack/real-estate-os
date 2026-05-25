from __future__ import annotations

from typing import Any, Dict

from agents.land_ledger_agent import LandLedgerAgent


def analyze_land_ledger_workbook_use_case(
    file_path: str,
    import_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Analyze an uploaded land ledger workbook from a prepared local file path."""
    return LandLedgerAgent().analyze_xls(file_path, import_metadata)


def enrich_land_ledger_use_case(
    land_ledger: Dict[str, Any],
    enrichment_options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run batch PNU, cadastral, official-price, and registry enrichment on a parsed ledger."""
    return LandLedgerAgent().enrich_analysis(land_ledger, enrichment_options or {})
