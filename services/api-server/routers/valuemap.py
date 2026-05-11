from fastapi import APIRouter, Query

from schemas.valuemap import ValuemapResponse

router = APIRouter(prefix="/api/valuemap", tags=["Valuemap"])


@router.get("/transactions", response_model=ValuemapResponse)
async def get_transactions(
    lat: float = Query(..., description="위도"),
    lng: float = Query(..., description="경도"),
    radius: int = Query(500, description="검색 반경 미터"),
):
    """Return nearby transaction comparables.

    The endpoint is intentionally mock-backed for the alpha build. It keeps the
    response shape stable so the frontend can later be wired to Valuemap or a
    licensed transaction database without changing UI code.
    """

    transactions = [
        {
            "id": "tx-1",
            "address": "역삼동 826-1",
            "transaction_date": "2026.02",
            "total_price_krw": "350억",
            "unit_price_krw": "1.60억/평",
            "property_type": "상업업무",
            "lat": lat + 0.0006,
            "lng": lng - 0.0026,
        },
        {
            "id": "tx-2",
            "address": "역삼동 825-9",
            "transaction_date": "2025.11",
            "total_price_krw": "280억",
            "unit_price_krw": "1.45억/평",
            "property_type": "상업업무",
            "lat": lat - 0.0004,
            "lng": lng - 0.0011,
        },
        {
            "id": "tx-3",
            "address": "역삼동 824-3",
            "transaction_date": "2025.08",
            "total_price_krw": "195억",
            "unit_price_krw": "1.30억/평",
            "property_type": "근린상가",
            "lat": lat - 0.0014,
            "lng": lng + 0.0014,
        },
        {
            "id": "tx-4",
            "address": "역삼동 830-2",
            "transaction_date": "2025.05",
            "total_price_krw": "420억",
            "unit_price_krw": "1.55억/평",
            "property_type": "상업업무",
            "lat": lat + 0.0011,
            "lng": lng + 0.0024,
        },
    ]

    return {
        "summary": {
            "avg_unit_price": "1.48억/평",
            "total_count": len(transactions),
            "radius": radius,
        },
        "transactions": transactions,
    }
