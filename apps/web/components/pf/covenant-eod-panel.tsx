"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Download, Loader2, ScrollText, ShieldAlert } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";

type Covenant = {
  code: string;
  category?: string;
  label: string;
  monitoringMetric?: string;
  thresholdType?: string;
  thresholdValue?: number | string | Array<string> | null;
  unit?: string | null;
  basis?: string;
  breachAction?: string;
  status?: string;
  currentValue?: number;
  breached?: boolean;
  sourceLocator?: { engineKey?: string; field?: string; sourceType?: string };
};

type EodCondition = {
  code: string;
  label: string;
  trigger?: string;
  thresholdValue?: number | string | null;
  unit?: string | null;
  severity?: string;
  cure?: string;
};

type CovenantEodResult = {
  schemaVersion: string;
  summary?: { covenantCount?: number; activeCovenantCount?: number; needsEvidenceCount?: number; eodConditionCount?: number; breachedCovenantCodes?: string[] };
  covenants: Covenant[];
  eodConditions: EodCondition[];
  dataQualityNotes: Array<{ code: string; reason: string }>;
  covenantSheet?: { schemaVersion?: string; projectName?: string | null; markdown?: string };
};

function artifactCovenantResult(value: unknown): CovenantEodResult | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const candidate = value as Partial<CovenantEodResult>;
  if (
    typeof candidate.schemaVersion !== "string" ||
    !Array.isArray(candidate.covenants) ||
    !Array.isArray(candidate.eodConditions) ||
    !Array.isArray(candidate.dataQualityNotes)
  ) {
    return null;
  }
  return candidate as CovenantEodResult;
}

function formatThreshold(value: Covenant["thresholdValue"], unit?: string | null): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.join(" → ");
  if (typeof value === "string") return value;
  if (unit === "ratio") return `${(value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%`;
  if (unit === "KRW") return `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원`;
  if (unit === "x") return `${value.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}x`;
  return value.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
}

const STATUS_ACTIVE = { text: "적용", cls: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" };
const STATUS_LABEL: Record<string, { text: string; cls: string }> = {
  active: STATUS_ACTIVE,
  needs_evidence: { text: "근거 필요", cls: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300" },
};

const SEVERITY_CLS: Record<string, string> = {
  critical: "border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200",
  high: "border-orange-300 bg-orange-50 text-orange-800 dark:border-orange-900 dark:bg-orange-950/30 dark:text-orange-200",
  medium: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200",
};

export function CovenantEodPanel({
  projectId,
  artifactResult,
  sourceArtifactKey,
}: {
  projectId: string;
  artifactResult?: unknown;
  sourceArtifactKey?: string;
}) {
  const [result, setResult] = useState<CovenantEodResult | null>(() =>
    artifactCovenantResult(artifactResult),
  );
  const [resultSource, setResultSource] = useState<"artifact" | "analysis" | "none">(
    artifactCovenantResult(artifactResult) ? "artifact" : "none",
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const parsed = artifactCovenantResult(artifactResult);
    setResult(parsed);
    setResultSource(parsed ? "artifact" : "none");
    setError(
      artifactResult !== null && artifactResult !== undefined && !parsed
        ? "선택한 PF artifact의 Covenant/EOD 결과 계약이 올바르지 않습니다."
        : null,
    );
  }, [artifactResult]);

  const loadAnalysisOnly = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/covenant-eod/evaluate`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`Covenant/EOD 산출 실패 (${res.status})`);
      const parsed = artifactCovenantResult(await res.json());
      if (!parsed) throw new Error("Covenant/EOD 응답 계약이 올바르지 않습니다.");
      setResult(parsed);
      setResultSource("analysis");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Covenant/EOD를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  function downloadSheet() {
    const md = result?.covenantSheet?.markdown;
    if (!md) return;
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `covenant-sheet-${projectId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ScrollText className="h-5 w-5 text-indigo-600" />
          <h2 className="text-lg font-black text-slate-950 dark:text-white">Covenant / EOD (기한이익상실)</h2>
        </div>
        <div className="flex items-center gap-3">
          {resultSource === "artifact" ? (
            <span className="max-w-56 truncate text-[11px] font-bold text-slate-500" title={sourceArtifactKey}>
              PF artifact · {sourceArtifactKey ?? "key 미확인"}
            </span>
          ) : resultSource === "analysis" ? (
            <span className="text-[11px] font-black text-amber-700 dark:text-amber-300">
              분석용 재산출 · 승인 근거 아님
            </span>
          ) : null}
          {result?.summary ? (
            <span className="text-xs font-semibold text-slate-500">
              커버넌트 {result.summary.activeCovenantCount ?? 0}/{result.summary.covenantCount ?? 0} · EOD {result.summary.eodConditionCount ?? 0}
              {result.summary.needsEvidenceCount ? ` · 근거 필요 ${result.summary.needsEvidenceCount}` : ""}
            </span>
          ) : null}
          {result?.covenantSheet?.markdown ? (
            <button
              type="button"
              onClick={downloadSheet}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-indigo-200 bg-indigo-50 px-2.5 text-xs font-bold text-indigo-700 dark:border-indigo-900 dark:bg-indigo-950/40 dark:text-indigo-300"
            >
              <Download className="h-3.5 w-3.5" />
              Covenant Sheet
            </button>
          ) : null}
          {!artifactCovenantResult(artifactResult) ? (
            <button
              type="button"
              onClick={() => void loadAnalysisOnly()}
              disabled={loading}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-2.5 text-xs font-bold text-amber-800 disabled:opacity-50 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
            >
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              분석용 재산출
            </button>
          ) : null}
        </div>
      </div>
      <p className="mb-4 text-xs text-slate-500">
        기본 화면은 선택한 하나의 PF-Credit artifact에 함께 저장된 Covenant/EOD만
        표시합니다. 독립 재산출 결과는 분석 전용이며 기존 artifact나 승인 상태를 갱신하지
        않습니다.
      </p>

      {loading ? (
        <div className="flex min-h-[120px] items-center justify-center text-sm font-bold text-slate-500">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />분석용 Covenant/EOD 산출 중입니다.
        </div>
      ) : error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">{error}</div>
      ) : result ? (
        <div className="space-y-5">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
                <tr>
                  <th className="px-3 py-2">커버넌트</th>
                  <th className="px-3 py-2">기준</th>
                  <th className="px-3 py-2">임계값</th>
                  <th className="px-3 py-2">위반 시 조치</th>
                  <th className="px-3 py-2">근거</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {result.covenants.map((cov) => {
                  const status = STATUS_LABEL[cov.status ?? "active"] ?? STATUS_ACTIVE;
                  return (
                    <tr key={cov.code} className={cov.breached ? "bg-rose-50/60 dark:bg-rose-950/20" : ""}>
                      <td className="px-3 py-3">
                        <div className="flex items-center gap-2">
                          <span className="font-black text-slate-950 dark:text-white">{cov.label}</span>
                          <span className={`rounded-full px-2 py-0.5 text-[10px] font-black ${status.cls}`}>{status.text}</span>
                          {cov.breached ? <span className="rounded-full bg-rose-600 px-2 py-0.5 text-[10px] font-black text-white">초과</span> : null}
                        </div>
                        {cov.monitoringMetric ? <p className="mt-0.5 text-[11px] text-slate-400">모니터링: {cov.monitoringMetric}</p> : null}
                      </td>
                      <td className="px-3 py-3 text-xs text-slate-500">{cov.basis ?? "—"}</td>
                      <td className="px-3 py-3">
                        <span className="font-black text-slate-950 dark:text-white">{formatThreshold(cov.thresholdValue, cov.unit)}</span>
                        {typeof cov.currentValue === "number" ? (
                          <p className={`mt-0.5 text-[11px] ${cov.breached ? "font-bold text-rose-600 dark:text-rose-300" : "text-slate-400"}`}>
                            현재 {formatThreshold(cov.currentValue, cov.unit)}
                          </p>
                        ) : null}
                      </td>
                      <td className="px-3 py-3 text-xs text-slate-600 dark:text-slate-300">{cov.breachAction ?? "—"}</td>
                      <td className="px-3 py-3 text-[11px] text-slate-400">{cov.sourceLocator?.field ?? cov.sourceLocator?.sourceType ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div>
            <h3 className="mb-2 flex items-center gap-2 text-sm font-black text-slate-950 dark:text-white">
              <ShieldAlert className="h-4 w-4 text-rose-600" />EOD (기한이익상실) 조건
            </h3>
            <div className="grid gap-2 md:grid-cols-2">
              {result.eodConditions.map((eod) => (
                <div key={eod.code} className={`rounded-lg border p-3 text-xs ${SEVERITY_CLS[eod.severity ?? "medium"] ?? SEVERITY_CLS.medium}`}>
                  <p className="font-black">{eod.label}</p>
                  <p className="mt-1 font-semibold">{eod.trigger}</p>
                  {eod.cure ? <p className="mt-1 opacity-80">보완: {eod.cure}</p> : null}
                </div>
              ))}
            </div>
          </div>

          {result.dataQualityNotes.length ? (
            <div className="rounded-lg bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              <p className="flex items-center gap-1 font-black"><AlertTriangle className="h-3.5 w-3.5" />근거 필요</p>
              <ul className="mt-1 space-y-0.5">
                {result.dataQualityNotes.map((note) => (
                  <li key={note.code}>{note.reason}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-slate-300 p-5 text-sm leading-6 text-slate-600 dark:border-slate-700 dark:text-slate-300">
          선택한 PF artifact에 Covenant/EOD 결과가 없습니다. 위의 분석용 재산출은 검토를
          돕기 위한 별도 실행이며 승인 패키지로 간주되지 않습니다.
        </div>
      )}
    </section>
  );
}
