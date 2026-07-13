"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ArrowLeft, Loader2, Plus, Scale, Trash2, Trophy } from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../../lib/workspace-context";
import { ProductPlanningPanel } from "../../../../components/product/product-planning-panel";

const ASSET_TYPES: { value: string; label: string }[] = [
  { value: "apartment", label: "아파트" },
  { value: "officetel", label: "오피스텔" },
  { value: "commercial", label: "상업시설" },
  { value: "knowledge_industry_center", label: "지식산업센터" },
  { value: "mixed_use", label: "복합개발" },
  { value: "logistics", label: "물류센터" },
  { value: "lodging", label: "생활숙박/호텔" },
  { value: "other", label: "기타" },
];

type ScenarioInput = {
  scenarioId: string;
  label: string;
  assetType: string;
  revenueEok: string;
  costEok: string;
};

type ComparisonRow = {
  scenarioId: string;
  label: string;
  assetType: string;
  assetTypeLabel: string;
  revenueMode?: string;
  recoveryHaircut?: number;
  ltvRiskThreshold?: number;
  totalRevenue?: number;
  totalCost?: number;
  developmentProfit?: number;
  profitMargin?: number;
  profitOnCost?: number;
  recommendedPfLimit?: number;
  ltvByRevenue?: number;
  ltcByCost?: number;
  exitSaleRate?: number;
  pfReadinessStatus?: string;
  isPfReviewReady?: boolean;
  missingFields?: string[];
  ltvExceedsThreshold?: boolean;
  status: string;
  rank?: number;
  error?: string;
};

type ComparisonResult = {
  schemaVersion: string;
  baseProgram: { landAreaSqm?: number; appliedFar?: number; appliedBcr?: number; address?: string };
  scenarioCount: number;
  comparableCount: number;
  insufficientDataCount: number;
  rows: ComparisonRow[];
  ranking: ComparisonRow[];
  recommendation: {
    label: string;
    assetTypeLabel: string;
    profitMargin: number;
    developmentProfit: number;
    note: string;
    ltvExceedsThreshold?: boolean;
  } | null;
  dataQualityNotes: { scenarioId: string; label: string; status: string; reasons: string[]; missingFields: string[] }[];
  notes?: string;
};

const EOK = 100_000_000;

function eok(value: unknown) {
  const n = Number(value ?? 0);
  return n ? `${(n / EOK).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억` : "—";
}

function pct(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%`;
}

let seedCounter = 0;
function newScenario(assetType: string, label: string): ScenarioInput {
  seedCounter += 1;
  return { scenarioId: `s-${seedCounter}`, label, assetType, revenueEok: "", costEok: "" };
}

export default function AssetComparisonPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioInput[]>([
    newScenario("apartment", "아파트안"),
    newScenario("mixed_use", "복합개발안"),
  ]);
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const ctx = await fetchWorkspaceContext(projectId, "feasibility");
      setContext(ctx);
    } catch {
      // Context is contextual only; comparison can still run on stored project state.
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  function updateScenario(id: string, patch: Partial<ScenarioInput>) {
    setScenarios((prev) => prev.map((s) => (s.scenarioId === id ? { ...s, ...patch } : s)));
  }

  function addScenario() {
    setScenarios((prev) => [...prev, newScenario("commercial", "신규 시나리오")]);
  }

  function removeScenario(id: string) {
    setScenarios((prev) => (prev.length > 1 ? prev.filter((s) => s.scenarioId !== id) : prev));
  }

  async function runComparison() {
    setRunning(true);
    setError(null);
    try {
      const payloadScenarios = scenarios.map((s) => {
        const overrides: Record<string, unknown> = {};
        const rev = Number(s.revenueEok);
        const cost = Number(s.costEok);
        const feasibility: Record<string, number> = {};
        if (s.revenueEok.trim() && Number.isFinite(rev)) feasibility.total_revenue = rev * EOK;
        if (s.costEok.trim() && Number.isFinite(cost)) feasibility.total_cost = cost * EOK;
        if (Object.keys(feasibility).length) overrides.feasibility_input = feasibility;
        return {
          scenarioId: s.scenarioId,
          label: s.label || s.assetType,
          assetType: s.assetType,
          ...(Object.keys(overrides).length ? { overrides } : {}),
        };
      });
      const res = await fetch(`${apiBase}/projects/${projectId}/asset-comparison/analyze`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ scenarios: payloadScenarios }),
      });
      if (!res.ok) throw new Error(`자산유형 비교 실행 실패 (${res.status})`);
      setResult((await res.json()) as ComparisonResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : "자산유형 비교에 실패했습니다.");
    } finally {
      setRunning(false);
    }
  }

  const projectName = displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`);

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Link
            href={`/project/${projectId}`}
            className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            aria-label="프로젝트로 돌아가기"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">{projectName}</p>
            <h1 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              <Scale className="h-7 w-7 text-indigo-600" />
              자산유형 사업성 비교
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              동일 토지·규제 조건에서 자산유형별 시나리오를 결정론적 사업성 엔진으로 재실행해 비교합니다. 수치를 새로 만들어내지 않으며, 근거가 없는 시나리오는 순위에서 제외합니다.
            </p>
          </div>
        </div>
      </header>

      {loading ? (
        <div className="flex min-h-[160px] items-center justify-center rounded-lg border border-slate-200 bg-white text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          프로젝트 원장을 불러오는 중입니다.
        </div>
      ) : null}

      {error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">
          {error}
        </div>
      ) : null}

      <Panel title="시나리오 설정">
        <p className="mb-3 text-xs text-slate-500">
          자산유형을 선택하고, 자산별로 다른 총수입·총사업비(억원)를 입력하면 그 값 기준으로 비교합니다. 비워두면 저장된 사업수지 값을 사용하고, 값이 없으면 &ldquo;자료 부족&rdquo;으로 표시됩니다.
        </p>
        <div className="space-y-2">
          {scenarios.map((s) => (
            <div
              key={s.scenarioId}
              className="grid grid-cols-1 gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3 md:grid-cols-[1.1fr_1fr_0.9fr_0.9fr_auto] md:items-center dark:border-slate-800 dark:bg-slate-800/50"
            >
              <input
                value={s.label}
                onChange={(e) => updateScenario(s.scenarioId, { label: e.target.value })}
                placeholder="시나리오명"
                className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
              <select
                value={s.assetType}
                onChange={(e) => updateScenario(s.scenarioId, { assetType: e.target.value })}
                className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              >
                {ASSET_TYPES.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
              <input
                value={s.revenueEok}
                onChange={(e) => updateScenario(s.scenarioId, { revenueEok: e.target.value })}
                inputMode="decimal"
                placeholder="총수입(억)"
                className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
              <input
                value={s.costEok}
                onChange={(e) => updateScenario(s.scenarioId, { costEok: e.target.value })}
                inputMode="decimal"
                placeholder="총사업비(억)"
                className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
              <button
                type="button"
                onClick={() => removeScenario(s.scenarioId)}
                disabled={scenarios.length <= 1}
                className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 text-slate-500 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-40 dark:border-slate-700"
                aria-label="시나리오 삭제"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={addScenario}
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            <Plus className="h-4 w-4" />
            시나리오 추가
          </button>
          <button
            type="button"
            onClick={() => void runComparison()}
            disabled={running}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-indigo-600 px-4 text-sm font-bold text-white disabled:opacity-50"
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Scale className="h-4 w-4" />}
            비교 실행
          </button>
        </div>
      </Panel>

      <ProductPlanningPanel projectId={projectId} />

      {result ? (
        <>
          <section className="grid gap-4 md:grid-cols-3">
            <Kpi label="비교 가능 시나리오" value={`${result.comparableCount} / ${result.scenarioCount}`} />
            <Kpi label="자료 부족 시나리오" value={`${result.insufficientDataCount}`} tone={result.insufficientDataCount ? "warn" : undefined} />
            <Kpi
              label="토지면적 / 용적률"
              value={`${(result.baseProgram.landAreaSqm ?? 0).toLocaleString("ko-KR")}㎡ / ${result.baseProgram.appliedFar ?? "—"}%`}
            />
          </section>

          {result.recommendation ? (
            <section className="rounded-lg border border-emerald-200 bg-emerald-50 p-5 dark:border-emerald-900 dark:bg-emerald-950/30">
              <div className="flex items-center gap-2 text-emerald-800 dark:text-emerald-200">
                <Trophy className="h-5 w-5" />
                <p className="text-lg font-black">
                  최고 이익률: {result.recommendation.label} ({result.recommendation.assetTypeLabel})
                </p>
              </div>
              <p className="mt-1 text-sm font-bold text-emerald-900 dark:text-emerald-100">
                개발이익률 {pct(result.recommendation.profitMargin)} · 개발이익 {eok(result.recommendation.developmentProfit)}원
                {result.recommendation.ltvExceedsThreshold ? " · ⚠ LTV 임계치 초과" : ""}
              </p>
              <p className="mt-2 text-xs text-emerald-800/80 dark:text-emerald-200/80">{result.recommendation.note}</p>
            </section>
          ) : (
            <section className="rounded-lg border border-amber-200 bg-amber-50 p-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              <p className="text-base font-black">순위를 매길 수 있는 시나리오가 없습니다.</p>
              <p className="mt-1 text-sm font-semibold">
                각 시나리오에 총수입·총사업비를 입력하거나, 사업수지 자료가 저장된 프로젝트에서 실행하세요. 값을 임의로 만들지 않습니다.
              </p>
            </section>
          )}

          <Panel title="비교 매트릭스">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-left text-sm">
                <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
                  <tr>
                    <th className="px-3 py-2">순위</th>
                    <th className="px-3 py-2">시나리오</th>
                    <th className="px-3 py-2">자산유형</th>
                    <th className="px-3 py-2">총수입</th>
                    <th className="px-3 py-2">총사업비</th>
                    <th className="px-3 py-2">개발이익</th>
                    <th className="px-3 py-2">이익률</th>
                    <th className="px-3 py-2">LTV(수입)</th>
                    <th className="px-3 py-2">권장PF한도</th>
                    <th className="px-3 py-2">PF준비</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {result.rows.map((row) => {
                    const insufficient = row.status !== "comparable";
                    return (
                      <tr key={row.scenarioId} className={insufficient ? "opacity-60" : row.rank === 1 ? "bg-emerald-50/60 dark:bg-emerald-950/20" : ""}>
                        <td className="px-3 py-3 font-black text-slate-950 dark:text-white">
                          {row.rank ? `#${row.rank}` : insufficient ? "—" : ""}
                        </td>
                        <td className="px-3 py-3 font-bold text-slate-950 dark:text-white">{row.label}</td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.assetTypeLabel}</td>
                        {insufficient ? (
                          <td className="px-3 py-3 text-xs font-bold text-amber-600 dark:text-amber-300" colSpan={7}>
                            {row.status === "error" ? `계산 오류: ${row.error ?? ""}` : "자료 부족 — 순위 제외"}
                          </td>
                        ) : (
                          <>
                            <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{eok(row.totalRevenue)}</td>
                            <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{eok(row.totalCost)}</td>
                            <td className={`px-3 py-3 font-bold ${Number(row.developmentProfit) > 0 ? "text-emerald-600 dark:text-emerald-300" : "text-rose-600 dark:text-rose-300"}`}>
                              {eok(row.developmentProfit)}
                            </td>
                            <td className="px-3 py-3 font-bold text-slate-950 dark:text-white">{pct(row.profitMargin)}</td>
                            <td className={`px-3 py-3 ${row.ltvExceedsThreshold ? "font-bold text-rose-600 dark:text-rose-300" : "text-slate-600 dark:text-slate-300"}`}>
                              {pct(row.ltvByRevenue)}
                              {row.ltvExceedsThreshold ? " ⚠" : ""}
                            </td>
                            <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{eok(row.recommendedPfLimit)}</td>
                            <td className="px-3 py-3 text-xs font-bold">
                              <span className={row.isPfReviewReady ? "text-emerald-600 dark:text-emerald-300" : "text-slate-500"}>
                                {row.isPfReviewReady ? "준비됨" : row.pfReadinessStatus === "leverage_review_required" ? "레버리지 검토" : "미준비"}
                              </span>
                            </td>
                          </>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>

          {result.dataQualityNotes.length ? (
            <Panel title="자료 부족 / 근거 진단">
              <ul className="space-y-2 text-sm">
                {result.dataQualityNotes.map((note) => (
                  <li key={note.scenarioId} className="rounded-md bg-amber-50 p-3 text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                    <span className="font-black">{note.label}</span>
                    <span className="ml-2 text-xs font-semibold">{note.reasons.join(", ")}</span>
                    {note.missingFields.length ? (
                      <p className="mt-1 text-xs text-amber-800/80 dark:text-amber-200/80">부족 항목: {note.missingFields.join(", ")}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}

          {result.notes ? <p className="text-xs text-slate-400">{result.notes}</p> : null}
        </>
      ) : (
        !loading && (
          <section className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900">
            시나리오를 설정하고 <span className="font-bold text-slate-700 dark:text-slate-200">비교 실행</span>을 누르면 자산유형별 사업성 비교 결과가 표시됩니다.
          </section>
        )
      )}
    </div>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "warn" }) {
  const color = tone === "warn" ? "text-amber-600 dark:text-amber-300" : "text-slate-950 dark:text-white";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm font-semibold text-slate-500">{label}</p>
      <p className={`mt-3 text-2xl font-black tracking-tight ${color}`}>{value}</p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h2 className="mb-4 text-lg font-black text-slate-950 dark:text-white">{title}</h2>
      {children}
    </section>
  );
}
