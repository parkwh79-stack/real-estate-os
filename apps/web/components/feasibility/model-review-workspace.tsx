"use client";

import { useState, type FormEvent } from "react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import type { CalculationRunRecord } from "../../lib/project-client";
import { modelReviewOptions, type Model } from "./model-review-options";

function money(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + "원" : "—";
}

const refinanceLabels: Record<string, string> = {
  funded: "실행 가정 충족", funding_after_maturity: "만기 이후 실행 예정",
  limit_exceeded: "차환 한도 초과", insufficient_closing_funds: "만기 정산 재원 부족",
};

export function ModelReviewWorkspace({ projectId }: { projectId: string }) {
  const [model, setModel] = useState<Model>("dated_financing");
  const config = modelReviewOptions[model];
  const [assumptions, setAssumptions] = useState("");
  const [reason, setReason] = useState("");
  const [runs, setRuns] = useState<CalculationRunRecord[]>([]);
  const [historyChecked, setHistoryChecked] = useState(false);
  const [selected, setSelected] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const endpoint = `${apiBase}/projects/${encodeURIComponent(projectId)}`;

  function changeModel(value: Model) {
    setModel(value);
    setAssumptions("");
    setReason("");
    setRuns([]);
    setHistoryChecked(false);
    setSelected("");
    setResult(null);
    setError("");
  }

  async function loadHistory() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${endpoint}/calculation-runs?engine_key=${model}&limit=5`, {
        cache: "no-store", headers: authHeaders("web"),
      });
      if (!response.ok) throw new Error("저장한 검토안을 불러오지 못했습니다.");
      const body = await response.json() as { records: CalculationRunRecord[] };
      setRuns(body.records);
      setHistoryChecked(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "조회에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  }

  function reopen(runId: string) {
    const run = runs.find((row) => row.calculationRunId === runId);
    setSelected(runId);
    setResult(run?.resultSnapshot ?? null);
    setAssumptions(run?.inputSnapshot?.assumptions ? JSON.stringify(run.inputSnapshot.assumptions, null, 2) : "");
    setReason(typeof run?.inputSnapshot?.reason === "string" ? run.inputSnapshot.reason : "");
  }

  async function calculate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    setResult(null);
    setSelected("");
    try {
      let input: unknown;
      try { input = JSON.parse(assumptions); }
      catch { throw new Error("입력 자료의 JSON 형식을 확인해 주세요."); }
      if (!input || Array.isArray(input) || typeof input !== "object") {
        throw new Error("검토 입력은 하나의 JSON 객체로 입력해 주세요.");
      }
      const response = await fetch(`${endpoint}/feasibility/model-review`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ model, assumptions: input, reason: reason.trim() }),
      });
      const body = await response.json() as Record<string, unknown>;
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "입력값과 분석 권한을 확인해 주세요.");
      setSelected(String(body.calculationRunId));
      setResult(body);
      setHistoryChecked(false);
      setRuns([]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "계산을 완료하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  const summary = result?.summary as Record<string, unknown> | undefined;
  const rows = result?.[config.detailKey];
  const refinance = result?.refinance as Record<string, unknown> | undefined;
  return <details className="rounded-xl border border-re-border bg-re-surface p-5">
    <summary className="cursor-pointer font-bold">날짜별 금융과 임대·회수 검토</summary>
    <form onSubmit={(event) => void calculate(event)} className="mt-4 space-y-3">
      <fieldset disabled={busy} className="space-y-3">
        <label className="grid gap-1 text-sm font-semibold">검토 방식
          <select value={model} onChange={(event) => changeModel(event.target.value as Model)}
            className="rounded-lg border border-re-border bg-re-surface p-2">
            {(Object.keys(modelReviewOptions) as Model[]).map((key) => <option value={key} key={key}>{modelReviewOptions[key].label}</option>)}
          </select>
        </label>
        <p className="text-sm text-re-secondary">{config.description}</p>
        <button type="button" onClick={() => {
          setAssumptions(JSON.stringify(config.example, null, 2));
          setReason(""); setResult(null); setSelected(""); setError("");
        }} className="rounded-lg border border-re-border px-3 py-2 text-sm font-semibold text-re-action">합성 예시 입력</button>
        <label className="grid gap-1 text-sm font-semibold">검토 입력 (JSON)
          <textarea className="min-h-48 rounded-lg border border-re-border bg-re-surface p-3 font-mono text-xs"
            spellCheck={false} value={assumptions} onChange={(event) => setAssumptions(event.target.value)} maxLength={240_000}
            required placeholder="예시를 불러오거나 검토할 입력을 붙여넣으세요." />
        </label>
        <label className="grid gap-1 text-sm font-semibold">입력 근거와 검토 사유
          <input className="rounded-lg border border-re-border bg-re-surface p-2" type="text" value={reason}
            onChange={(event) => setReason(event.target.value)} maxLength={1000} required />
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" className="rounded-lg bg-re-action px-3 py-2 text-sm font-semibold text-re-on-action disabled:opacity-50"
            disabled={!reason.trim()}>{busy ? "처리 중" : "분석안 계산·저장"}</button>
          <button type="button" onClick={() => void loadHistory()}
            className="rounded-lg border border-re-border px-3 py-2 text-sm font-semibold">저장한 검토안 보기</button>
        </div>
        {runs.length ? <label className="grid gap-1 text-sm font-semibold">저장한 검토안
          <select className="rounded-lg border border-re-border bg-re-surface p-2" value={runs.some((run) => run.calculationRunId === selected) ? selected : ""}
            onChange={(event) => reopen(event.target.value)}>
            <option value="">검토안을 선택하세요</option>
            {runs.map((run) => <option key={run.calculationRunId} value={run.calculationRunId}>
              {typeof run.inputSnapshot?.reason === "string" ? run.inputSnapshot.reason : config.label} · {run.createdAt ? new Date(run.createdAt).toLocaleString("ko-KR") : run.calculationRunId}
            </option>)}
          </select>
        </label> : historyChecked && <p className="text-sm text-re-secondary">저장한 검토안이 없습니다.</p>}
      </fieldset>
    </form>
    {error && <p role="alert" className="mt-3 text-sm text-re-danger">{error}</p>}
    {result && <section aria-label="저장된 분석 결과" className="mt-4 space-y-3">
      <p className="text-sm font-bold">저장된 분석 결과 <span className="font-normal text-re-secondary">· 검토용</span></p>
      {result.status === "partial" && <p className="text-sm text-re-danger">만기 미상환분의 추가 이자·비용을 입력하면 매각 정산과 배분을 계산할 수 있습니다.</p>}
      {model === "dated_financing" && Number(summary?.maturitiesPending) > 0 &&
        <p className="text-sm text-re-secondary">기준일 이후 만기가 {Number(summary?.maturitiesPending)}건 남았습니다.</p>}
      {refinance && <p className="text-sm">차환 검토 한도 {money(refinance.sizingLimit)} · 순유입 {money(refinance.netProceeds)}
        {" · "}{refinanceLabels[String(refinance.status)] ?? "조건 확인"}
      </p>}
      <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {config.metrics.map(([key, label]) => <div key={key} className="rounded-lg border border-re-border p-3">
          <dt className="text-sm text-re-secondary">{label}</dt><dd className="mt-1 font-bold">{money(summary?.[key])}</dd>
        </div>)}
      </dl>
      {Array.isArray(rows) && rows.length > 0 && <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead><tr className="border-b border-re-border">
            {config.detailColumns.map(([key, label]) => <th key={key} scope="col" className="p-2">{label}</th>)}
          </tr></thead>
          <tbody>{(rows as Record<string, unknown>[]).map((row, index) => <tr key={index} className="border-b border-re-border">
            {config.detailColumns.map(([key, , numeric]) => <td key={key} className="whitespace-nowrap p-2">
              {numeric ? money(row[key]) : String(row[key] ?? "—")}
            </td>)}
          </tr>)}</tbody>
        </table>
      </div>}
      <details className="text-sm"><summary className="cursor-pointer font-semibold">계산 내역</summary>
        <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-re-subtle p-3 text-xs">{JSON.stringify(result, null, 2)}</pre>
      </details>
    </section>}
  </details>;
}
