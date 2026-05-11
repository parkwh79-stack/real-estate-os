"use client";

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  Loader2,
  MapPin,
  Scale,
  Search,
  ShieldAlert,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import { KakaoMap } from "../../components/map/kakao-map";
import {
  demoProject,
  transactions as fallbackTransactions,
  type Transaction,
} from "../../lib/platform-data";

interface TransactionResponse {
  summary: {
    avg_unit_price: string;
    total_count: number;
  };
  transactions: Transaction[];
}

interface RegulationChecklistItem {
  item: string;
  ok: boolean | null;
  detail: string;
  law: string;
}

interface RegulationResult {
  status: "compliant" | "review_needed" | "non_compliant";
  zone: string;
  plannedFar: number;
  plannedBcr: number;
  checklist: RegulationChecklistItem[];
  summary: string;
  hasRealApi: boolean;
}

interface PnuResult {
  pnu: string;
  address: string;
  jimok: string;
  area: number | null;
  zones: Array<{ name: string; detail?: string }>;
  error?: string;
}

const defaultSummary = {
  avg_unit_price: "1.48억/평",
  total_count: fallbackTransactions.length,
};

export default function AnalysisPage() {
  const [transactions, setTransactions] = useState<Transaction[]>(fallbackTransactions);
  const [summary, setSummary] = useState(defaultSummary);
  const [isLoading, setIsLoading] = useState(true);
  const [regLoading, setRegLoading] = useState(false);
  const [regResult, setRegResult] = useState<RegulationResult | null>(null);
  const [pnuResult, setPnuResult] = useState<PnuResult | null>(null);

  useEffect(() => {
    const fetchTransactions = async () => {
      try {
        const response = await fetch(
          "http://127.0.0.1:8000/api/valuemap/transactions?lat=37.4979&lng=127.0276&radius=500",
        );

        if (!response.ok) {
          return;
        }

        const data = (await response.json()) as TransactionResponse;
        setTransactions(data.transactions);
        setSummary(data.summary);
      } catch {
        setTransactions(fallbackTransactions);
        setSummary(defaultSummary);
      } finally {
        setIsLoading(false);
      }
    };

    void fetchTransactions();
  }, []);

  const handleRegCheck = async () => {
    setRegLoading(true);
    setRegResult(null);
    setPnuResult(null);

    try {
      const [pnuResponse, regulationResponse] = await Promise.all([
        fetch(`/api/pnu-lookup?pnu=${demoProject.pnu}`),
        fetch("/api/regulation-check", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            zone: "일반상업지역",
            plannedFar: 760,
            plannedBcr: 54,
            address: demoProject.address,
          }),
        }),
      ]);

      if (pnuResponse.ok) {
        setPnuResult((await pnuResponse.json()) as PnuResult);
      }

      if (regulationResponse.ok) {
        setRegResult((await regulationResponse.json()) as RegulationResult);
      }
    } finally {
      setRegLoading(false);
    }
  };

  return (
    <div className="grid min-h-[calc(100vh-8rem)] gap-4 xl:grid-cols-[1.35fr_0.75fr]">
      <section className="flex min-h-[560px] flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="border-b border-slate-200 p-4 dark:border-slate-800">
          <div className="flex flex-col gap-3 md:flex-row">
            <label className="flex h-11 flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 text-sm dark:border-slate-800 dark:bg-slate-950">
              <Search className="h-4 w-4 text-slate-400" />
              <input
                type="text"
                defaultValue={demoProject.address}
                className="w-full bg-transparent font-medium text-slate-900 outline-none dark:text-white"
              />
            </label>
            <button
              type="button"
              className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
            >
              <MapPin className="h-4 w-4" />
              대상지 분석
            </button>
          </div>
        </div>
        <KakaoMap transactions={transactions} />
      </section>

      <aside className="space-y-4 overflow-y-auto pb-4">
        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-start justify-between gap-3">
            <div>
              <span className="rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-xs font-bold text-indigo-700 dark:border-indigo-900 dark:bg-indigo-950/40 dark:text-indigo-300">
                일반상업지역
              </span>
              <h2 className="mt-3 text-xl font-black text-slate-950 dark:text-white">
                {demoProject.name}
              </h2>
              <p className="mt-1 text-sm text-slate-500">{demoProject.address}</p>
              <p className="mt-1 font-mono text-xs text-slate-400">PNU {demoProject.pnu}</p>
            </div>
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800">
              <Building2 className="h-5 w-5 text-slate-600 dark:text-slate-300" />
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3">
            <Fact label="대지면적" value="2,586.8㎡" />
            <Fact label="계획 용적률" value="760%" tone="good" />
            <Fact label="예상 연면적" value="19,659㎡" />
            <Fact label="개발 가능성" value="82점" tone="warn" />
          </div>
        </section>

        <section className="rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between border-b border-slate-200 p-5 dark:border-slate-800">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-50 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
                <TrendingUp className="h-4 w-4" />
              </div>
              <h3 className="font-bold text-slate-950 dark:text-white">주변 거래사례</h3>
            </div>
            <span className="text-xs text-slate-500">반경 500m</span>
          </div>

          <div className="space-y-4 p-5">
            <div className="rounded-lg border border-sky-100 bg-sky-50 p-4 dark:border-sky-900 dark:bg-sky-950/30">
              <p className="text-xs font-semibold text-sky-700 dark:text-sky-300">
                최근 상업업무 평균 단가
              </p>
              {isLoading ? (
                <div className="mt-2 h-7 w-32 animate-pulse rounded-md bg-sky-100 dark:bg-sky-900" />
              ) : (
                <p className="mt-1 text-2xl font-black text-slate-950 dark:text-white">
                  {summary.avg_unit_price}
                </p>
              )}
            </div>

            <div className="space-y-2">
              {transactions.map((transaction) => (
                <div
                  key={transaction.id}
                  className="rounded-lg border border-slate-200 p-3 dark:border-slate-800"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-semibold text-slate-950 dark:text-white">
                        {transaction.address}
                      </p>
                      <p className="mt-1 text-xs text-slate-500">
                        {transaction.transaction_date} 거래 · {transaction.property_type}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="font-bold text-slate-950 dark:text-white">
                        {transaction.total_price_krw}
                      </p>
                      <p className="mt-1 text-xs text-slate-500">
                        {transaction.unit_price_krw}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between border-b border-slate-200 p-5 dark:border-slate-800">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-50 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">
                <Scale className="h-4 w-4" />
              </div>
              <h3 className="font-bold text-slate-950 dark:text-white">인허가 적합성</h3>
            </div>
            <span className="text-xs text-slate-500">MOLIT/LAW API ready</span>
          </div>

          <div className="p-5">
            {!regResult && !regLoading && (
              <div className="space-y-4">
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/30">
                  <div className="flex gap-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                    <p className="text-sm leading-6 text-amber-900 dark:text-amber-200">
                      계획 용적률 760%, 건폐율 54% 기준으로 용도지역 허용 범위와
                      추가 검토 항목을 점검합니다.
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={handleRegCheck}
                  className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-violet-600 px-4 text-sm font-semibold text-white hover:bg-violet-700"
                >
                  <Scale className="h-4 w-4" />
                  인허가 체크 실행
                </button>
              </div>
            )}

            {regLoading && (
              <div className="flex items-center justify-center gap-3 py-8 text-sm text-slate-500">
                <Loader2 className="h-5 w-5 animate-spin text-violet-600" />
                규제 데이터 확인 중
              </div>
            )}

            {regResult && (
              <div className="space-y-4">
                <div
                  className={`rounded-lg border p-4 ${
                    regResult.status === "compliant"
                      ? "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/30"
                      : "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30"
                  }`}
                >
                  <div className="flex gap-2">
                    {regResult.status === "compliant" ? (
                      <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
                    ) : (
                      <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
                    )}
                    <div>
                      <p className="font-bold text-slate-950 dark:text-white">
                        {regResult.status === "compliant"
                          ? "기본 기준 적합"
                          : "조건 검토 필요"}
                      </p>
                      <p className="mt-1 text-sm leading-6 text-slate-700 dark:text-slate-300">
                        {regResult.summary}
                      </p>
                    </div>
                  </div>
                </div>

                <div className="space-y-2">
                  {regResult.checklist.map((item) => (
                    <div
                      key={item.item}
                      className="rounded-lg border border-slate-200 p-3 dark:border-slate-800"
                    >
                      <div className="flex gap-2">
                        {item.ok === true ? (
                          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                        ) : item.ok === false ? (
                          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" />
                        ) : (
                          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                        )}
                        <div>
                          <p className="text-sm font-semibold text-slate-950 dark:text-white">
                            {item.item}
                          </p>
                          <p className="mt-1 text-xs leading-5 text-slate-500">
                            {item.detail}
                          </p>
                          <p className="mt-1 text-xs text-indigo-600 dark:text-indigo-300">
                            {item.law}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                {pnuResult && (
                  <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
                    <p className="text-xs font-bold text-slate-600 dark:text-slate-300">
                      토지이용계획 조회
                    </p>
                    <p className="mt-1 text-sm text-slate-900 dark:text-white">
                      {pnuResult.address}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      지목 {pnuResult.jimok} · 면적{" "}
                      {pnuResult.area ? `${pnuResult.area.toLocaleString("ko-KR")}㎡` : "-"}
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
      </aside>
    </div>
  );
}

function Fact({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "warn";
}) {
  const color =
    tone === "good"
      ? "text-emerald-600 dark:text-emerald-300"
      : tone === "warn"
        ? "text-amber-600 dark:text-amber-300"
        : "text-slate-950 dark:text-white";

  return (
    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className={`mt-1 font-black ${color}`}>{value}</p>
    </div>
  );
}
