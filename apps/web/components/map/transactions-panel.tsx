"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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

function manWon(won?: number): string {
  return typeof won === "number" ? `${Math.round(won / 10_000).toLocaleString("ko-KR")}만원` : "—";
}
function manPerPy(won?: number): string {
  if (!won) return "—";
  return `${Math.round(won / 10_000).toLocaleString("ko-KR")}만/평`;
}

function defaultDealYm(): string {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
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
  const [dealYm, setDealYm] = useState(defaultDealYm);
  const [result, setResult] = useState<{ query: string; data?: TxResponse; error?: string }>();
  const [pendingQuery, setPendingQuery] = useState<string>();
  const request = useRef<AbortController | null>(null);

  const onRecordsRef = useRef(onRecords);
  onRecordsRef.current = onRecords;
  const onComplexesRef = useRef(onComplexes);
  onComplexesRef.current = onComplexes;

  const effectiveLawd = lawdCd || manualLawd.trim();
  const params = new URLSearchParams({ lawd_cd: effectiveLawd, deal_ym: dealYm, asset_type: assetType, deal_kind: dealKind });
  if (geocode) {
    params.set("geocode", "true");
    params.set("geocode_limit", "30");
  }
  if (aggregate) params.set("aggregate", "true");
  const query = params.toString();
  const validQuery = /^\d{5}$/.test(effectiveLawd) && /^\d{4}(0[1-9]|1[0-2])$/.test(dealYm);
  const loading = pendingQuery === query;
  const activeResult = result?.query === query ? result : undefined;

  const load = useCallback(async () => {
    if (!validQuery) {
      setResult({ query, error: "법정동코드 5자리와 조회월 6자리(YYYYMM)를 입력하세요." });
      return;
    }
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setPendingQuery(query);
    setResult(undefined);
    onRecordsRef.current?.([]);
    onComplexesRef.current?.([]);
    try {
      const res = await fetch(`${apiBase}/api/market/transactions?${query}`, {
        cache: "no-store",
        headers: authHeaders("web"),
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20_000)]),
      });
      if (!res.ok) throw new Error(`실거래가 조회 실패 (${res.status})`);
      const json = (await res.json()) as TxResponse;
      if (controller.signal.aborted) return;
      if (json.diagnostic?.status === "skipped") throw new Error("실거래가 조회 서비스가 아직 연결되지 않았습니다.");
      if (json.diagnostic?.status !== "ok" && json.diagnostic?.status !== "empty") {
        throw new Error("국토교통부 실거래가를 불러오지 못했습니다. 잠시 후 다시 조회해 주세요.");
      }
      if (!Array.isArray(json.records)) throw new Error("실거래가 조회 응답을 확인하지 못했습니다.");
      setResult({ query, data: json });
      onRecordsRef.current?.(json.records ?? []);
      onComplexesRef.current?.(json.complexes ?? []);
    } catch (err) {
      if (!controller.signal.aborted) {
        setResult({ query, error: err instanceof Error && err.name === "Error" ? err.message : "실거래가를 불러오지 못했습니다. 잠시 후 다시 조회해 주세요." });
      }
    } finally {
      if (!controller.signal.aborted) setPendingQuery(undefined);
    }
  }, [query, validQuery]);

  useEffect(() => () => { request.current?.abort(); }, [query]);

  function clearResult() {
    request.current?.abort();
    setResult(undefined);
    setPendingQuery(undefined);
    onRecordsRef.current?.([]);
    onComplexesRef.current?.([]);
  }

  useEffect(() => {
    if (lawdCd) void load();
    // A parcel context loads its initial result. Changes to query filters require 조회.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lawdCd]);

  const records = activeResult?.data?.records ?? [];
  const summary = activeResult?.data?.summary;

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
              onChange={(e) => { clearResult(); setManualLawd(e.target.value); }}
              inputMode="numeric"
              maxLength={5}
              placeholder="예: 11650"
              className="ml-2 w-24 rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900"
            />
          </label>
        ) : (
          <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            법정동 {lawdCd}
          </span>
        )}
        <select aria-label="자산 유형" value={assetType} onChange={(e) => { clearResult(); setAssetType(e.target.value); }} className="rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900">
          {ASSET_TYPES.map((a) => (
            <option key={a.value} value={a.value}>{a.label}</option>
          ))}
        </select>
        <select aria-label="거래 유형" value={dealKind} onChange={(e) => { clearResult(); setDealKind(e.target.value as "trade" | "rent"); }} className="rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900">
          <option value="trade">매매</option>
          <option value="rent">전월세</option>
        </select>
        <input
          value={dealYm}
          onChange={(e) => { clearResult(); setDealYm(e.target.value); }}
          aria-label="조회월 (YYYYMM)"
          inputMode="numeric"
          maxLength={6}
          placeholder="YYYYMM"
          className="w-24 rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900"
        />
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading || !validQuery}
          className="inline-flex h-8 items-center gap-1 rounded-lg bg-slate-900 px-3 text-xs font-bold text-white disabled:opacity-40 dark:bg-white dark:text-slate-950"
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          조회
        </button>
      </div>

      {activeResult?.error ? <p role="alert" className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{activeResult.error}</p> : null}

      {activeResult?.data ? (
        <div role="status" className="mt-3 flex flex-wrap gap-4 text-sm">
          <span className="font-semibold text-slate-600 dark:text-slate-300">
            이번 조회 거래 <strong className="text-slate-950 dark:text-white">{records.length}</strong>건
          </span>
          {summary?.avgPricePerPy ? (
            <span className="font-semibold text-sky-700 dark:text-sky-300">평균 평단가 {manPerPy(summary.avgPricePerPy)}</span>
          ) : null}
        </div>
      ) : null}

      {records.length ? (
        <div className="mt-3 max-h-80 overflow-auto">
          <table className="w-full min-w-[24rem] text-sm">
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
                  <td className="px-2 py-1.5 text-slate-600 dark:text-slate-300">{typeof r.areaSqm === "number" ? `${Math.round(r.areaSqm)}㎡` : "—"}</td>
                  <td className="px-2 py-1.5 text-slate-500">{r.transactionDate ?? "—"}</td>
                  <td className="px-2 py-1.5 text-right text-slate-900 dark:text-white">
                    {dealKind === "trade" ? (
                      <span>
                        {manWon(r.totalPrice)} <span className="text-xs text-slate-400">{manPerPy(r.pricePerPy)}</span>
                      </span>
                    ) : (
                      <span>
                        {manWon(r.depositKRW)}
                        <span className="text-xs text-slate-400"> / {manWon(r.monthlyRentKRW)}</span>
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
