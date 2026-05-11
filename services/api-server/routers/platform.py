from typing import Any, Dict

from fastapi import APIRouter

from agents.platform_benchmark_agent import PlatformBenchmarkAgent

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/benchmark/reference-model")
def get_platform_reference_model():
    """Return the real-estate finance platform reference model.

    This endpoint is intentionally data-light. It lets the frontend render the
    target operating model without sending a full PF project payload.
    """

    return PlatformBenchmarkAgent().analyze({})


@router.post("/benchmark/analyze")
def analyze_platform_benchmark(input_data: Dict[str, Any] | None = None):
    """Analyze platform expansion gaps for a project or portfolio payload."""

    return PlatformBenchmarkAgent().analyze(input_data or {})
