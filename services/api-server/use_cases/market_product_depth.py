"""Market-Product depth use case (P1-04).

On-demand deterministic analysis: 제공주체(시행사/매각주관사/공공/AI) 분류·비교,
상품유형별 분양성 score, 흡수율 모델, AI 추가필요작업 판단. Reuses the latest
marketability analysis when present, else runs it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from agents.market_product_depth_agent import MarketProductDepthAgent
from use_cases.marketability_review import analyze_marketability_use_case


def analyze_market_product_depth_use_case(
    project_data: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    options = options or {}
    marketability = (
        project_data.get("marketabilityAnalysis")
        or project_data.get("marketability")
    )
    if not isinstance(marketability, dict) or not marketability:
        try:
            marketability = analyze_marketability_use_case(project_data, options)
        except Exception:
            marketability = {}
    return MarketProductDepthAgent().analyze(project_data, marketability, options=options)
