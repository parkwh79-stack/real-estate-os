import { type ReactNode } from "react";

export interface FinancialMetricProps {
  label: string;
  value: ReactNode;
  unit?: string;
  basis?: string;
  asOfDate?: string;
  source?: ReactNode;
  version?: string;
  status?: ReactNode;
  className?: string;
}

export function FinancialMetric({
  label,
  value,
  unit,
  basis,
  asOfDate,
  source,
  version,
  status,
  className = "",
}: FinancialMetricProps) {
  return (
    <div className={`rounded-re-control border border-re-border bg-re-surface p-3 ${className}`}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-semibold text-re-secondary">{label}</p>
        {status}
      </div>
      <p className="re-financial-number mt-2 text-right text-xl font-bold text-re-text">
        {value}
        {unit ? <span className="ml-1 text-xs font-semibold text-re-secondary">{unit}</span> : null}
      </p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px] leading-4 text-re-muted">
        {basis ? <><dt>기준</dt><dd className="text-right">{basis}</dd></> : null}
        {asOfDate ? <><dt>기준일</dt><dd className="text-right">{asOfDate}</dd></> : null}
        {version ? <><dt>버전</dt><dd className="text-right">{version}</dd></> : null}
        {source ? <><dt>근거</dt><dd className="text-right">{source}</dd></> : null}
      </dl>
    </div>
  );
}
