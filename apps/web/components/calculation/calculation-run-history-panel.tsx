"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, RefreshCw, GitCompare, AlertTriangle, ArrowRight } from "lucide-react";
import {
  fetchCalculationRuns,
  groupRunsByEngine,
  engineLabel,
  metricMap,
  metricDelta,
  formatMetricValue,
  formatRunTimestamp,
  shortRunId,
  blockerCodes,
  type CalculationRun,
} from "../../lib/calculation-runs";

/**
 * Calculation run history + comparison. Lists persisted CalculationRuns per
 * engine (Waterfall / Unsold LTV / Tranche Recovery / Stress / …), and lets the
 * reviewer diff two runs of the same engine — metric deltas and blocker
 * (SENIOR_SHORTFALL / UNSOLD_LTV_BREACH) appearance/resolution across runs.
 */
export function CalculationRunHistoryPanel({ projectId }: { projectId: string }) {
  // A project change must discard its selections and pending requests together.
  return <ProjectCalculationRunHistory key={projectId} projectId={projectId} />;
}

function ProjectCalculationRunHistory({ projectId }: { projectId: string }) {
  const [runs, setRuns] = useState<CalculationRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeEngine, setActiveEngine] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [openedRunId, setOpenedRunId] = useState<string | null>(null);
  const pendingRequest = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    pendingRequest.current?.abort();
    const controller = new AbortController();
    pendingRequest.current = controller;
    setLoading(true);
    setError(null);
    setRuns([]);
    try {
      const all = await fetchCalculationRuns(projectId, { limit: 300, signal: controller.signal });
      if (!controller.signal.aborted) setRuns(all);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "계산 이력을 불러오지 못했습니다.");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    return () => pendingRequest.current?.abort();
  }, [load]);

  const groups = useMemo(() => groupRunsByEngine(runs), [runs]);

  // Default the active engine to the first group once loaded.
  useEffect(() => {
    if (groups.length && !groups.some((group) => group.engineKey === activeEngine)) {
      setActiveEngine(groups[0]!.engineKey);
      setSelected([]);
      setOpenedRunId(null);
    }
  }, [groups, activeEngine]);

  const activeRuns = useMemo(
    () => groups.find((g) => g.engineKey === activeEngine)?.runs ?? [],
    [groups, activeEngine],
  );

  // Reset the comparison selection when the engine changes.
  const selectEngine = useCallback((engineKey: string) => {
    setActiveEngine(engineKey);
    setSelected([]);
    setOpenedRunId(null);
  }, []);

  const toggleSelect = useCallback((runId: string) => {
    setSelected((prev) => {
      if (prev.includes(runId)) return prev.filter((id) => id !== runId);
      // Keep at most two selected — drop the oldest selection.
      return prev.length >= 2 ? [prev[prev.length - 1]!, runId] : [...prev, runId];
    });
  }, []);

  const comparison = useMemo(() => {
    if (selected.length !== 2) return null;
    const older = activeRuns.find((r) => r.calculationRunId === selected[0]);
    const newer = activeRuns.find((r) => r.calculationRunId === selected[1]);
    if (!older || !newer) return null;
    // Order by timestamp so "이전 → 이후" reads correctly regardless of click order.
    const [a, b] = String(older.createdAt ?? "").localeCompare(String(newer.createdAt ?? "")) <= 0
      ? [older, newer]
      : [newer, older];
    const mapA = metricMap(a);
    const mapB = metricMap(b);
    const keys = [...new Set([...mapA.keys(), ...mapB.keys()])].sort();
    const rows = keys.map((key) => {
      const ma = mapA.get(key);
      const mb = mapB.get(key);
      const delta = metricDelta(ma, mb);
      return { key, before: ma, after: mb, delta };
    });
    const blockersA = new Set(blockerCodes(a));
    const blockersB = new Set(blockerCodes(b));
    const added = [...blockersB].filter((c) => !blockersA.has(c));
    const resolved = [...blockersA].filter((c) => !blockersB.has(c));
    return { a, b, rows, added, resolved };
  }, [selected, activeRuns]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white p-5 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900">
        <Loader2 className="h-4 w-4 animate-spin" /> 계산 이력을 불러오는 중…
      </div>
    );
  }

  return (
    <section aria-label="계산 실행 이력" className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <GitCompare className="h-5 w-5 text-slate-700 dark:text-slate-200" />
          <h2 className="text-lg font-black text-slate-950 dark:text-white">계산 실행 이력 · 비교</h2>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 px-2.5 text-xs font-bold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <RefreshCw className="h-3.5 w-3.5" /> 새로고침
        </button>
      </div>

      {error ? <p role="alert" className="mb-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {error ? null : groups.length === 0 ? (
        <p className="rounded-lg bg-slate-50 p-5 text-sm text-slate-500 dark:bg-slate-800/70">
          저장된 계산 실행이 없습니다. 사업수지·현금흐름·시나리오(Waterfall/미분양 LTV/회수)·스트레스 계산을 실행하면 각 실행이
          여기서 저장된 입력·결과를 다시 열고 비교할 수 있습니다.
        </p>
      ) : (
        <>
          {/* Engine tabs */}
          <div className="flex flex-wrap gap-1.5">
            {groups.map((g) => {
              const active = g.engineKey === activeEngine;
              const hasBlocker = g.runs.some((r) => blockerCodes(r).length > 0);
              return (
                <button
                  key={g.engineKey}
                  type="button"
                  onClick={() => selectEngine(g.engineKey)}
                  className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-bold transition-colors ${
                    active
                      ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
                  }`}
                >
                  {engineLabel(g.engineKey)}
                  <span className={`rounded-full px-1.5 text-[10px] ${active ? "bg-white/20" : "bg-slate-200 dark:bg-slate-700"}`}>{g.runs.length}</span>
                  {hasBlocker ? <AlertTriangle className="h-3 w-3 text-rose-400" /> : null}
                </button>
              );
            })}
          </div>

          {/* Run list for the active engine */}
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
                <tr>
                  <th className="px-3 py-2">비교</th>
                  <th className="px-3 py-2">실행 시각</th>
                  <th className="px-3 py-2">Run ID</th>
                  <th className="px-3 py-2">버전</th>
                  <th className="px-3 py-2">지표</th>
                  <th className="px-3 py-2">Blocker</th>
                  <th className="px-3 py-2">저장 내용</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {activeRuns.map((run) => {
                  const blockers = blockerCodes(run);
                  const isSelected = selected.includes(run.calculationRunId);
                  return (
                    <tr key={run.calculationRunId} className={isSelected ? "bg-sky-50/60 dark:bg-sky-950/20" : undefined}>
                      <td className="px-3 py-2.5">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(run.calculationRunId)}
                          aria-label="비교 대상 선택"
                          className="h-4 w-4 accent-sky-600"
                        />
                      </td>
                      <td className="px-3 py-2.5 font-semibold text-slate-800 dark:text-slate-200">{formatRunTimestamp(run)}</td>
                      <td className="px-3 py-2.5 font-mono text-xs text-slate-500">{shortRunId(run.calculationRunId)}</td>
                      <td className="px-3 py-2.5 text-xs text-slate-500">{run.engineVersion ?? "—"}</td>
                      <td className="px-3 py-2.5 text-xs text-slate-500">{run.outputMetrics?.length ?? 0}개</td>
                      <td className="px-3 py-2.5">
                        {blockers.length ? (
                          <span className="inline-flex flex-wrap gap-1">
                            {blockers.map((code) => (
                              <span key={code} className="rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-black text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
                                {code}
                              </span>
                            ))}
                          </span>
                        ) : (
                          <span className="text-xs text-emerald-600 dark:text-emerald-400">없음</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <button type="button" aria-expanded={openedRunId === run.calculationRunId}
                          onClick={() => setOpenedRunId((current) => current === run.calculationRunId ? null : run.calculationRunId)}
                          className="whitespace-nowrap rounded border border-slate-300 px-2 py-1 text-xs font-bold dark:border-slate-600">
                          입력·결과 보기
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {activeRuns.filter((run) => run.calculationRunId === openedRunId).map((run) => (
            <SavedRunDetails key={run.calculationRunId} run={run} />
          ))}

          <p className="mt-2 text-xs text-slate-400">
            최근 300개 실행을 표시합니다. {" "}
            {selected.length === 2
              ? "선택한 두 실행을 비교합니다."
              : `비교하려면 같은 엔진의 실행 2개를 선택하세요. (현재 ${selected.length}/2)`}
          </p>

          {/* Comparison */}
          {comparison ? (
            <div className="mt-4 rounded-lg border border-sky-200 bg-sky-50/50 p-4 dark:border-sky-900 dark:bg-sky-950/20">
              {(!comparison.a.engineVersion || !comparison.b.engineVersion || comparison.a.engineVersion !== comparison.b.engineVersion) ? (
                <p className="mb-3 text-sm text-amber-700 dark:text-amber-300">계산 버전이 다르거나 기록되지 않았습니다. 결과 차이에 계산 방식의 변경이 포함될 수 있습니다.</p>
              ) : null}
              <div className="mb-3 flex flex-wrap items-center gap-2 text-sm font-bold text-slate-800 dark:text-slate-100">
                <span className="font-mono text-xs">{shortRunId(comparison.a.calculationRunId)}</span>
                <span className="text-xs font-normal text-slate-500">{formatRunTimestamp(comparison.a)}</span>
                <ArrowRight className="h-4 w-4 text-slate-400" />
                <span className="font-mono text-xs">{shortRunId(comparison.b.calculationRunId)}</span>
                <span className="text-xs font-normal text-slate-500">{formatRunTimestamp(comparison.b)}</span>
              </div>

              {(comparison.added.length || comparison.resolved.length) ? (
                <div className="mb-3 flex flex-wrap gap-1.5">
                  {comparison.added.map((code) => (
                    <span key={`add-${code}`} className="rounded bg-rose-100 px-2 py-0.5 text-xs font-bold text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
                      + {code} 발생
                    </span>
                  ))}
                  {comparison.resolved.map((code) => (
                    <span key={`res-${code}`} className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-bold text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
                      − {code} 해소
                    </span>
                  ))}
                </div>
              ) : (
                <p className="mb-3 text-xs text-slate-500">두 실행 사이 blocker 변화 없음.</p>
              )}

              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
                    <tr>
                      <th className="px-3 py-2">지표</th>
                      <th className="px-3 py-2 text-right">이전</th>
                      <th className="px-3 py-2 text-right">이후</th>
                      <th className="px-3 py-2 text-right">변화</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {comparison.rows.map((row) => {
                      const changed = row.delta !== null && row.delta !== 0;
                      const beforeStr = formatMetricValue(row.before);
                      const afterStr = formatMetricValue(row.after);
                      const nonNumericChange = row.delta === null && beforeStr !== afterStr;
                      return (
                        <tr key={row.key} className={changed || nonNumericChange ? "bg-amber-50/40 dark:bg-amber-950/10" : undefined}>
                          <td className="px-3 py-2 font-mono text-xs text-slate-700 dark:text-slate-300">{row.key}</td>
                          <td className="px-3 py-2 text-right text-slate-600 dark:text-slate-300">{beforeStr}</td>
                          <td className="px-3 py-2 text-right font-semibold text-slate-900 dark:text-white">{afterStr}</td>
                          <td className="px-3 py-2 text-right text-xs font-bold">
                            {row.delta === null ? (
                              nonNumericChange ? <span className="text-amber-600 dark:text-amber-400">변경</span> : <span className="text-slate-300">—</span>
                            ) : row.delta === 0 ? (
                              <span className="text-slate-300">0</span>
                            ) : (
                              <span className={row.delta > 0 ? "text-rose-600 dark:text-rose-400" : "text-emerald-600 dark:text-emerald-400"}>
                                {row.delta > 0 ? "▲ +" : "▼ "}
                                {Math.abs(row.delta) >= 1000 ? row.delta.toLocaleString("ko-KR") : Number(row.delta.toFixed(4))}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function SavedRunDetails({ run }: { run: CalculationRun }) {
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const hasInput = Boolean(run.inputSnapshot && Object.keys(run.inputSnapshot).length);
  const hasResult = Boolean(run.resultSnapshot && Object.keys(run.resultSnapshot).length);
  function download() {
    setDownloadError(null);
    try {
      // The visible record is the sole export source; never fetch live project inputs.
      const url = URL.createObjectURL(new Blob([JSON.stringify(run, null, 2)], { type: "application/json" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `calculation-${run.calculationRunId.replace(/[^a-zA-Z0-9_-]/g, "_")}.json`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch {
      setDownloadError("저장 내용을 다운로드하지 못했습니다. 다시 시도해 주세요.");
    }
  }
  return (
    <section aria-label="저장된 계산 입력과 결과" className="mt-4 space-y-3 rounded-lg border border-slate-200 p-4 dark:border-slate-700">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-bold">{engineLabel(run.engineKey)} · 저장된 입력과 결과</h3>
        <button type="button" onClick={download} className="rounded border border-slate-300 px-3 py-2 text-xs font-bold dark:border-slate-600">저장 내용 다운로드 (JSON)</button>
      </div>
      <p className="break-all text-xs text-slate-500">실행 번호: {run.calculationRunId} · {formatRunTimestamp(run)} · 버전: {run.engineVersion ?? "미기록"}</p>
      <p className="text-sm text-slate-600 dark:text-slate-300">실행 당시 저장한 내용입니다. 화면과 다운로드는 같은 이력을 사용합니다.</p>
      <p className="text-sm text-slate-600 dark:text-slate-300">
        {run.inputDigest?.capturePolicy === "effective_request_v1" && hasInput
          ? "계산에 사용한 요청이 저장되어 있습니다."
          : "과거 이력은 일부 입력만 저장되었을 수 있습니다. 전체 입력 저장 여부는 확인되지 않았습니다."}
      </p>
      {downloadError ? <p role="alert" className="text-sm text-rose-600">{downloadError}</p> : null}
      <div className="grid gap-3 md:grid-cols-2">
        {([ ["저장된 입력 원문", run.inputSnapshot, hasInput], ["저장된 결과 원문", run.resultSnapshot, hasResult] ] as const).map(([label, snapshot, present]) => (
          <details key={label} className="min-w-0 rounded bg-slate-50 p-3 dark:bg-slate-800">
            <summary className="cursor-pointer text-sm font-bold">{label}</summary>
            {present ? <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(snapshot, null, 2)}</pre>
              : <p className="mt-3 text-sm text-amber-700 dark:text-amber-300">미기록 — 이 실행에는 {label.includes("입력") ? "입력" : "결과"} 원문이 저장되지 않았습니다.</p>}
          </details>
        ))}
      </div>
    </section>
  );
}
