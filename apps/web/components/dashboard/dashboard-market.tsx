"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { ArrowUpRight, Map as MapIcon } from "lucide-react";
import { useMemo, useState } from "react";
import type { MapIntelligenceData, TxMarker } from "../map/map-intelligence";
import { TransactionsPanel, type TxRecord } from "../map/transactions-panel";

const CompactMap = dynamic(
  () => import("../map/map-intelligence").then((module) => module.MapIntelligence),
  {
    ssr: false,
    loading: () => <div role="status" className="flex h-80 items-center justify-center rounded-re-control bg-re-subtle text-sm text-re-secondary sm:h-[360px]">지도를 불러오는 중입니다.</div>,
  },
);

// No selected parcel or inferred position: a country map is contextual only.
const CONTEXT_MAP: MapIntelligenceData = {
  center: null,
  parcelGeoJson: { type: "FeatureCollection", features: [] },
};

export function DashboardMarket() {
  const [showMap, setShowMap] = useState(false);
  const [records, setRecords] = useState<TxRecord[]>([]);
  const markers = useMemo<TxMarker[]>(() => records.flatMap((record, index) => {
    const { lat, lng } = record;
    if (typeof lat !== "number" || typeof lng !== "number" || !Number.isFinite(lat) || !Number.isFinite(lng)
      || Math.abs(lat) > 90 || Math.abs(lng) > 180) return [];
    const amount = record.dealKind === "rent" ? record.depositKRW : record.totalPrice;
    return [{
      lat, lng,
      label: record.address || `거래 ${index + 1}`,
      sublabel: typeof amount === "number" && Number.isFinite(amount)
        ? `${Math.round(amount).toLocaleString("ko-KR")}원` : undefined,
      tone: record.dealKind === "rent" ? "rent" : "trade",
      value: record.pricePerPy,
    }];
  }), [records]);

  return (
    <section aria-labelledby="dashboard-market-title" data-testid="dashboard-market" className="min-w-0 rounded-re-panel border border-re-border bg-re-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="dashboard-market-title" className="flex items-center gap-2 text-lg font-semibold text-re-text">
            <MapIcon aria-hidden="true" className="h-5 w-5 shrink-0 text-re-action" />
            지도·실거래가
          </h2>
          <p className="mt-2 text-sm leading-6 text-re-secondary">지역과 거래월을 선택해 공공 실거래 자료를 확인하세요. 조회 결과는 서버에 저장하지 않습니다.</p>
        </div>
        <Link href="/analysis" prefetch={false} className="inline-flex min-h-10 shrink-0 items-center gap-1 rounded-re-control px-2 text-sm font-medium text-re-action hover:bg-re-subtle">
          상세 지도·토지 분석 <ArrowUpRight aria-hidden="true" className="h-4 w-4" />
        </Link>
      </div>

      <div className="mt-4 grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-medium text-re-text">{markers.length ? `좌표가 확인된 거래 ${markers.length.toLocaleString("ko-KR")}건` : "전국 배경지도"}</p>
            <button type="button" aria-expanded={showMap} aria-controls="dashboard-market-map" onClick={() => setShowMap((value) => !value)}
              className="min-h-10 rounded-re-control border border-re-border px-3 text-sm font-medium text-re-text hover:bg-re-subtle">
              {showMap ? "지도 접기" : "지도 보기"}
            </button>
          </div>
          <div id="dashboard-market-map">
            {showMap ? <CompactMap data={CONTEXT_MAP} transactions={markers} compact /> : (
              <div className="flex min-h-32 items-center justify-center rounded-re-control border border-dashed border-re-border bg-re-subtle px-5 py-6 text-center text-sm leading-6 text-re-secondary">
                지도 보기로 전국 지도를 살펴볼 수 있습니다.
              </div>
            )}
          </div>
          <p className="text-xs leading-5 text-re-secondary">
            {markers.length
              ? "조회 응답에 실제 좌표가 있는 거래만 지도에 표시합니다."
              : "선택된 대상지가 없는 기본 지도입니다. 좌표가 없는 거래는 목록에서 확인할 수 있습니다."}
          </p>
        </div>
        <div className="min-w-0">
          <TransactionsPanel geocode={false} onRecords={setRecords} />
        </div>
      </div>
    </section>
  );
}
