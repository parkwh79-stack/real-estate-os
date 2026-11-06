import { type ReactNode } from "react";

import {
  ApprovalStatusBadge,
  DataQualityBadge,
  ReportEligibilityBadge,
  ValueNatureBadge,
  type ApprovalStatusPresentation,
  type DataQualityStatusPresentation,
  type ReportEligibilityPresentation,
  type ValueNaturePresentation,
} from "./status-axis";

export type FinancialMetricRawValue = number | string | bigint | null;

export interface FinancialMetricProps {
  /** Stable domain key such as `returns.equity_irr`. */
  metricKey?: string;
  label: string;
  /** Compatibility display value. Prefer `displayValue` for new callers. */
  value?: ReactNode;
  /** Human-formatted value; never use it as a calculation input. */
  displayValue?: ReactNode;
  /** Machine value preserved separately from the formatted display value. */
  rawValue?: FinancialMetricRawValue;
  /** Compatibility display unit. Prefer `displayUnit` for new callers. */
  unit?: string;
  /** Unit of `rawValue`, for example `KRW`, `ratio`, or `months`. */
  rawUnit?: string;
  /** Human display unit, for example `억원`, `%`, or `x`. */
  displayUnit?: string;
  currency?: string;
  basis?: string;
  scenario?: string;
  asOfDate?: string;
  source?: ReactNode;
  sourceId?: string;
  sourceSnapshotId?: string;
  calculationRunId?: string;
  /** Compatibility calculation version. Prefer `calculationVersion`. */
  version?: string;
  calculationVersion?: string;
  approvalStatus?: ApprovalStatusPresentation;
  reportEligibility?: ReportEligibilityPresentation;
  dataQualityStatus?: DataQualityStatusPresentation;
  /** Business meaning only; this does not establish lineage or authority. */
  valueNature?: ValueNaturePresentation;
  /** Compatibility slot for caller-owned status content. */
  status?: ReactNode;
  className?: string;
}

function MetadataRow({
  label,
  children,
  mono = false,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <>
      <dt>{label}</dt>
      <dd
        className={`min-w-0 break-all text-right text-re-secondary ${mono ? "font-mono" : ""}`}
      >
        {children}
      </dd>
    </>
  );
}

export function FinancialMetric({
  metricKey,
  label,
  value,
  displayValue,
  rawValue,
  unit,
  rawUnit,
  displayUnit,
  currency,
  basis,
  scenario,
  asOfDate,
  source,
  sourceId,
  sourceSnapshotId,
  calculationRunId,
  version,
  calculationVersion,
  approvalStatus,
  reportEligibility,
  dataQualityStatus,
  valueNature,
  status,
  className = "",
}: FinancialMetricProps) {
  const renderedValue =
    displayValue ??
    value ??
    (rawValue === null || rawValue === undefined ? "—" : String(rawValue));
  const renderedUnit = displayUnit ?? unit;
  const renderedVersion = calculationVersion ?? version;
  const rawValueText =
    rawValue === null || rawValue === undefined ? undefined : String(rawValue);

  return (
    <div
      className={`rounded-re-control border border-re-border bg-re-surface p-3 ${className}`}
      data-metric-key={metricKey}
      data-raw-value={rawValueText}
      data-raw-unit={rawUnit}
      data-display-unit={renderedUnit}
      data-scenario={scenario}
      data-source-snapshot-id={sourceSnapshotId}
      data-calculation-run-id={calculationRunId}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-semibold text-re-secondary">{label}</p>
        <div className="flex flex-wrap justify-end gap-1">
          {status}
          {valueNature ? <ValueNatureBadge status={valueNature} /> : null}
          {dataQualityStatus ? (
            <DataQualityBadge status={dataQualityStatus} />
          ) : null}
          {reportEligibility ? (
            <ReportEligibilityBadge status={reportEligibility} />
          ) : null}
          {approvalStatus ? (
            <ApprovalStatusBadge status={approvalStatus} />
          ) : null}
        </div>
      </div>
      <p className="re-financial-number mt-2 text-right text-xl font-bold text-re-text">
        {renderedValue}
        {renderedUnit ? (
          <span className="ml-1 text-xs font-semibold text-re-secondary">
            {renderedUnit}
          </span>
        ) : null}
      </p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px] leading-4 text-re-muted">
        {rawValueText !== undefined && displayValue !== undefined ? (
          <MetadataRow label="원값" mono>
            {rawValueText}
          </MetadataRow>
        ) : null}
        {rawUnit ? <MetadataRow label="원 단위">{rawUnit}</MetadataRow> : null}
        {currency ? <MetadataRow label="통화">{currency}</MetadataRow> : null}
        {basis ? <MetadataRow label="기준">{basis}</MetadataRow> : null}
        {scenario ? (
          <MetadataRow label="시나리오">{scenario}</MetadataRow>
        ) : null}
        {asOfDate ? <MetadataRow label="기준일">{asOfDate}</MetadataRow> : null}
        {renderedVersion ? (
          <MetadataRow label="계산 버전" mono>
            {renderedVersion}
          </MetadataRow>
        ) : null}
        {calculationRunId ? (
          <MetadataRow label="계산 Run" mono>
            {calculationRunId}
          </MetadataRow>
        ) : null}
        {sourceSnapshotId ? (
          <MetadataRow label="근거 Snapshot" mono>
            {sourceSnapshotId}
          </MetadataRow>
        ) : null}
        {sourceId ? (
          <MetadataRow label="근거 ID" mono>
            {sourceId}
          </MetadataRow>
        ) : null}
        {source ? <MetadataRow label="근거">{source}</MetadataRow> : null}
      </dl>
    </div>
  );
}
