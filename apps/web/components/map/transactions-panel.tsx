"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, TrendingUp } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";

export type TxRecord = {
  address?: string;
  dealType?: string;
  dealKind?: string;
  areaSqm?: number;
  totalPrice?: number;
  pricePerPy?: number;
  depositKRW?: number;
  monthlyRentKRW?: number;
  transactionDate?: string;
  floor?: number;
  lat?: number;
  lng?: number;
};
export type ComplexRecord = {
  complexName?: string;
  umdNm?: string;
  count?: number;
  tradeCount?: number;
  medianPricePerPy?: number;
  minPricePerPy?: number;
  maxPricePerPy?: number;
  latestDealDate?: string;
  lat?: number;
  lng?: number;
};
type TxResponse = {
  summary?: { totalCount?: number; tradeCount?: number; rentCount?: number; avgPricePerPy?: number };
  records?: TxRecord[];
  complexes?: ComplexRecord[];
  diagnostic?: { status?: string; message?: string };
};

const ASSET_TYPES = [
  { value: "apartment", label: "아파트" },
  { value: "officetel", label: "오피스텔" },
  { value: "row_house", label: "연립·다세대" },
  { value: "detached_house", label: "단독·다가구" },
  { value: "land", label: "토지" },
  { value: "commercial", label: "상업·업무" },
];

function eok(won?: number): string {
  if (!won) return "—";
  return `${(won / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억`;
}
function manPerPy(won?: number): string {
  if (!won) return "—";
  return `${Math.round(won / 10_000).toLocaleString("ko-KR")}만/평`;
}

function defaultDealYm(): string {
  const d = new Date();
  d.setMonth(d.getMonth() - 1); // last full month
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** 주변 실거래가 (국토부 RTMS public API). lawdCd derives from a parcel PNU.
 *  With `geocode`, records carry lat/lng; `onRecords` surfaces them (e.g. for map pins). */
export function TransactionsPanel({
  lawdCd,
  geocode = false,
  aggregate = false,
  onRecords,
  onComplexes,
}: {
  lawdCd?: string;
  geocode?: boolean;
  aggregate?: boolean;
  onRecords?: (records: TxRecord[]) => void;
  onComplexes?: (complexes: ComplexRecord[]) => void;
}) {
  const [manualLawd, setManualLawd] = useState("");
  const [assetType, setAssetType] = useState("apartment");
  const [dealKind, setDealKind] = useState<"trade" | "rent">("trade");
  const [dealYm, setDealYm] = useState(defaultDealYm());
  const [data, setData] = useState<TxResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onRecordsRef = useRef(onRecords);
  onRecordsRef.current = onRecords;
  const onComplexesRef = useRef(onComplexes);
  onComplexesRef.current = onComplexes;

  const effectiveLawd = useMemo(() => lawdCd || manualLawd.trim(), [lawdCd, manualLawd]);

  const load = useCallback(async () => {
    if (!effectiveLawd) {
      setError("법정동코드(5자리)가 필요합니다.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = { lawd_cd: effectiveLawd, deal_ym: dealYm, asset_type: assetType, deal_kind: dealKind };
      if (geocode) {
        params.geocode = "true";
        params.geocode_limit = "30";
      }
      if (aggregate) params.aggregate = "true";
      const res = await fetch(`${apiBase}/api/market/transactions?${new URLSearchParams(params)}`, { cache: "no-store", headers: authHeaders("web") });
      if (!res.ok) throw new Error(`실거래가 조회 실패 (${res.status})`);
      const json = (await res.json()) as TxResponse;
      setData(json);
      onRecordsRef.current?.(json.records ?? []);
      onComplexesRef.current?.(json.complexes ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "실거래가를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [effectiveLawd, dealYm, assetType, dealKind, geocode, aggregate]);

  useEffect(() => {
    if (effectiveLawd) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveLawd]);

  const records = data?.records ?? [];
  const summary = data?.summary;
  const diag = data?.diagnostic;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-50 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
          <TrendingUp className="h-4 w-4" />
        </div>
        <div>
          <h2 className="font-black text-slate-950 dark:text-white">주변 실거래가</h2>
          <p className="text-[11px] font-semibold text-slate-500">국토교통부 실거래가 공개시스템 · 출처 명시</p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-2">
        {!lawdCd ? (
          <label className="text-xs font-semibold text-slate-500">
            법정동코드
            <input
              value={manualLawd}
              onChange={(e) => setManualLawd(e.target.value)}
              placeholder="예: 11650"
              className="ml-2 w-24 rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900"
            />
          </label>
        ) : (
          <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            법정동 {lawdCd}
          </span>
        )}
        <select value={assetType} onChange={(e) => setAssetType(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900">
          {ASSET_TYPES.map((a) => (
            <option key={a.value} value={a.value}>{a.label}</option>
          ))}
        </select>
        <select value={dealKind} onChange={(e) => setDealKind(e.target.value as "trade" | "rent")} className="rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900">
          <option value="trade">매매</option>
          <option value="rent">전월세</option>
        </select>
        <input
          value={dealYm}
          onChange={(e) => setDealYm(e.target.value)}
          placeholder="YYYYMM"
          className="w-24 rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900"
        />
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading || !effectiveLawd}
          className="inline-flex h-8 items-center gap-1 rounded-lg bg-slate-900 px-3 text-xs font-bold text-white disabled:opacity-40 dark:bg-white dark:text-slate-950"
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          조회
        </button>
      </div>

      {error ? <p className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {summary ? (
        <div className="mt-3 flex flex-wrap gap-4 text-sm">
          <span className="font-semibold text-slate-600 dark:text-slate-300">
            거래 <strong className="text-slate-950 dark:text-white">{summary.totalCount ?? 0}</strong>건
            (매매 {summary.tradeCount ?? 0} · 전월세 {summary.rentCount ?? 0})
          </span>
          {summary.avgPricePerPy ? (
            <span className="font-semibold text-sky-700 dark:text-sky-300">평균 평단가 {manPerPy(summary.avgPricePerPy)}</span>
          ) : null}
        </div>
      ) : null}

      {diag && diag.status !== "ok" && !records.length ? (
        <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          {diag.status === "skipped"
            ? "data.go.kr 실거래가 키(DATA_GO_KR_API_KEY)를 설정하면 실제 거래내역이 표시됩니다."
            : diag.status === "forbidden"
              ? `${diag.message} (다른 자산유형/거래는 매매부터 시도해 보세요.)`
              : diag.message ?? "표시할 거래가 없습니다."}
        </p>
      ) : null}

      {records.length ? (
        <div className="mt-3 max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-slate-50 text-left text-xs font-bold text-slate-500 dark:bg-slate-950/60">
              <tr>
                <th className="px-2 py-1.5">주소</th>
                <th className="px-2 py-1.5">전용</th>
                <th className="px-2 py-1.5">거래일</th>
                <th className="px-2 py-1.5 text-right">{dealKind === "trade" ? "거래가 / 평단가" : "보증금 / 월세"}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {records.map((r, i) => (
                <tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-950/40">
                  <td className="px-2 py-1.5 text-slate-900 dark:text-white">{r.address ?? "—"}</td>
                  <td className="px-2 py-1.5 text-slate-600 dark:text-slate-300">{r.areaSqm ? `${r.areaSqm}㎡` : "—"}</td>
                  <td className="px-2 py-1.5 text-slate-500">{r.transactionDate ?? "—"}</td>
                  <td className="px-2 py-1.5 text-right text-slate-900 dark:text-white">
                    {dealKind === "trade" ? (
                      <span>
                        {eok(r.totalPrice)} <span className="text-xs text-slate-400">{manPerPy(r.pricePerPy)}</span>
                      </span>
                    ) : (
                      <span>
                        {eok(r.depositKRW)}
                        {r.monthlyRentKRW ? <span className="text-xs text-slate-400"> / {Math.round(r.monthlyRentKRW / 10_000)}만</span> : null}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
