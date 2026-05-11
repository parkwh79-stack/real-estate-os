from pydantic import BaseModel, Field


class TransactionItem(BaseModel):
    id: str = Field(..., description="거래 고유 ID")
    address: str = Field(..., description="지번 또는 도로명 주소")
    transaction_date: str = Field(..., description="거래년월")
    total_price_krw: str = Field(..., description="총 거래금액")
    unit_price_krw: str = Field(..., description="평당 거래단가")
    property_type: str = Field(..., description="부동산 유형")
    lat: float = Field(..., description="위도")
    lng: float = Field(..., description="경도")


class TransactionSummary(BaseModel):
    avg_unit_price: str = Field(..., description="평균 평당 단가")
    total_count: int = Field(..., description="조회 거래 건수")


class ValuemapResponse(BaseModel):
    summary: TransactionSummary
    transactions: list[TransactionItem]
