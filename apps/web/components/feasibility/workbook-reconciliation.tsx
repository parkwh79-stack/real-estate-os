"use client";

import { useState } from "react";
import { apiBase, authHeaders } from "../../lib/auth-client";

type Metric = {
  key: string;
  label: string;
  unit: string;
  workbookValue: number | null;
  workingValue: number | null;
  differenceWon: number | null;
  status: string;
};

type Reconciliation = {
  status: string;
  sourceDocument?: { originalFileName?: string; fileName?: string };
  rows: Array<Metric & {
    sourceCell?: string;
    sourceUnit?: string;
    nextAction?: string;
    workingReason?: string;
  }>;
  periodComparison?: { message: string };
  calculationComparison?: { message: string; metrics?: Metric[] };
};

function valueText(value: number | null | undefined, unit: string) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value.toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + (unit === "KRW" ? "원" : "");
}

export function WorkbookReconciliation({ projectId }: { projectId: string }) {
  const [result, setResult] = useState<Reconciliation | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    setResult(null);
    setError("");
    try {
      const response = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/feasibility-input/workbook-reconciliation`, {
        cache: "no-store", headers: authHeaders("web"),
      });
      if (!response.ok) throw new Error("현재 프로젝트의 Excel 대사 자료를 불러오지 못했습니다.");
      setResult(await response.json() as Reconciliation);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Excel 대사를 완료하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return <details className="rounded-xl border border-re-border bg-re-surface p-5">
    <summary className="cursor-pointer font-bold">Excel과 검토 입력 대사</summary>
    <p className="mt-2 text-sm text-re-secondary">원본 셀의 저장값과 현재 사업수지 입력을 원 단위로 대조합니다.</p>
    <button type="button" onClick={() => void load()} disabled={busy}
      className="mt-3 rounded-lg border border-re-border px-3 py-2 text-sm font-semibold text-re-action disabled:opacity-50">
      {busy ? "대사 중" : "최신 입력 대사"}
    </button>
    {error && <p role="alert" className="mt-2 text-sm text-re-danger">{error}</p>}
    {result && <div className="mt-4 space-y-3 text-sm">
      <p className="font-semibold">{result.sourceDocument?.originalFileName ?? result.sourceDocument?.fileName ?? "연결된 원본"}</p>
      {result.rows.length ? <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead><tr className="border-b border-re-border">
            <th className="p-2" scope="col">항목과 출처</th><th className="p-2" scope="col">Excel 입력</th>
            <th className="p-2" scope="col">검토 입력</th><th className="p-2" scope="col">차이</th>
            <th className="p-2" scope="col">확인할 일</th>
          </tr></thead>
          <tbody>{result.rows.map((row) => <tr key={row.key} className="border-b border-re-border">
            <th className="min-w-36 p-2 font-medium" scope="row">{row.label}
              {row.sourceCell && <span className="block text-xs text-re-secondary">{row.sourceCell} {row.sourceUnit}</span>}
            </th>
            <td className="whitespace-nowrap p-2">{valueText(row.workbookValue, row.unit)}</td>
            <td className="whitespace-nowrap p-2">{valueText(row.workingValue, row.unit)}</td>
            <td className="whitespace-nowrap p-2">{valueText(row.differenceWon, row.unit)}</td>
            <td className="min-w-44 p-2">{row.nextAction ?? (row.status === "matched" ? "일치" : "입력 확인")}
              {row.workingReason && <span className="block text-xs text-re-secondary">{row.workingReason}</span>}
            </td>
          </tr>)}</tbody>
        </table>
      </div> : <p>대사할 Excel 입력을 확인할 수 없습니다. 보관된 사업수지의 검토 상태를 확인해 주세요.</p>}
      {result.periodComparison?.message && <p>{result.periodComparison.message}</p>}
      {result.calculationComparison?.message && <p className="font-semibold">{result.calculationComparison.message}</p>}
      <dl className="grid gap-2 sm:grid-cols-2">
        {result.calculationComparison?.metrics?.map((metric) => <div key={metric.key} className="rounded-lg border border-re-border p-3">
          <dt>{metric.label}</dt>
          <dd className="text-re-secondary">Excel 입력 {valueText(metric.workbookValue, metric.unit)} · 검토 입력 {valueText(metric.workingValue, metric.unit)}</dd>
        </div>)}
      </dl>
    </div>}
  </details>;
}
