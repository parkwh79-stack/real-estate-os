export type ReportInputReconciliation = {
  schemaVersion: string; summary: string; limitation: string;
  rows: { key: string; label: string; status: string; missingCount: number;
    values: { slot: string; label: string; calculationRunId: string; value: number | null;
      unit: string; currency: string; unitSource: string; status: string }[] }[];
  sameFamilyChecks: { label: string; status: string; leftRunId: string; rightRunId: string }[];
};

const labels: Record<string, string> = { different: "차이 있음", same: "확인한 입력 일치",
  missing: "입력 누락", unit_mismatch: "단위 확인 필요", insufficient: "대조 대상 부족" };
const integerDisplay = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
const wholeNumber = (value: number) => integerDisplay.format(Math.abs(value) < 0.5 ? 0 : value);

export function ReportInputReconciliationPanel({ value }: { value?: ReportInputReconciliation }) {
  if (!value) return <p className="mt-3 text-sm text-amber-700">이 저장 보고서에는 입력 대사 기록이 없습니다. 계산을 선택해 새 초안을 만들면 대사 결과도 저장됩니다.</p>;
  return <section aria-label="저장 보고서의 입력 대사" className="mt-5 min-w-0 rounded-lg border border-slate-300 p-4 dark:border-slate-700">
    <h4 className="font-bold">선택 계산의 입력 대조</h4>
    <p className="mt-2 font-bold">{value.summary}</p>
    <p className="mt-2 text-sm leading-6 text-slate-500">{value.limitation}</p>
    <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2">{value.rows.map((row) => <div key={row.key} className="min-w-0 rounded-lg bg-slate-50 p-3 dark:bg-slate-800">
      <p className="text-sm font-bold">{row.label} · {labels[row.status] || "미확인"}</p>
      {row.missingCount > 0 ? <p className="mt-1 text-xs text-amber-700">{row.missingCount}개 계산의 입력이 미기록입니다.</p> : null}
      <dl className="mt-2 space-y-2 text-sm">{row.values.map((cell) => <div key={cell.slot}>
        <dt>{cell.label}</dt><dd className="break-words font-bold">{cell.status === "present" && typeof cell.value === "number"
          ? `${wholeNumber(cell.value)} ${cell.unit === "KRW" ? "원" : cell.unit}` : "미기록"}</dd>
        <dd className="break-all text-xs text-slate-500">계산 기록: {cell.calculationRunId}</dd>
        <dd className="text-xs text-slate-500">{cell.currency} / {cell.unit} · {cell.unitSource === "captured" ? "저장 단위" : "계산 API 기본 단위"}</dd>
      </div>)}</dl>
    </div>)}</div>
    {value.sameFamilyChecks.length ? <div className="mt-4 space-y-2 text-sm"><h5 className="font-bold">같은 계산 계열의 전체 업무입력</h5>
      {value.sameFamilyChecks.map((pair) => <div key={`${pair.leftRunId}:${pair.rightRunId}`}>
        <p>{pair.label} · <strong>{labels[pair.status] || "미확인"}</strong></p>
        <p className="break-all text-xs text-slate-500">{pair.leftRunId} / {pair.rightRunId}</p>
      </div>)}
    </div> : null}
    <p className="mt-3 text-sm">표시값은 정수로 반올림합니다. 입력·결과 JSON 다운로드에서 각 계산의 정확한 원본 입력을 함께 확인할 수 있습니다.</p>
  </section>;
}
