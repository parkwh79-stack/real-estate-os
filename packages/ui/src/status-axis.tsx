import { type ReactNode } from "react";

type StatusDefinition = { label: string; className: string };

const BASE = "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-bold";
const TONES = {
  neutral: "border-re-border bg-re-subtle text-re-secondary",
  info: "border-blue-200 bg-blue-50 text-re-info dark:border-blue-800 dark:bg-blue-950/30",
  success: "border-emerald-200 bg-emerald-50 text-re-success dark:border-emerald-800 dark:bg-emerald-950/30",
  warning: "border-amber-200 bg-amber-50 text-re-warning dark:border-amber-800 dark:bg-amber-950/30",
  danger: "border-red-200 bg-red-50 text-re-danger dark:border-red-800 dark:bg-red-950/30",
} as const;

const EXECUTION = {
  planned: { label: "계획", className: TONES.neutral },
  pending: { label: "대기", className: TONES.neutral },
  running: { label: "실행 중", className: TONES.info },
  completed: { label: "실행 완료", className: TONES.info },
  blocked: { label: "실행 차단", className: TONES.warning },
  failed: { label: "실행 실패", className: TONES.danger },
} satisfies Record<string, StatusDefinition>;

const APPROVAL = {
  not_started: { label: "미검토", className: TONES.neutral },
  in_review: { label: "검토 중", className: TONES.info },
  approved: { label: "승인", className: TONES.success },
  conditional: { label: "조건부 승인", className: TONES.warning },
  rejected: { label: "반려", className: TONES.danger },
  revoked: { label: "승인 철회", className: TONES.danger },
} satisfies Record<string, StatusDefinition>;

const EVIDENCE = {
  missing: { label: "근거 없음", className: TONES.danger },
  candidate: { label: "공식 검색 후보", className: TONES.warning },
  located: { label: "근거 위치 확인", className: TONES.info },
  verified: { label: "원문 검증", className: TONES.success },
} satisfies Record<string, StatusDefinition>;

const QUALITY = {
  missing: { label: "누락", className: TONES.danger },
  partial: { label: "부분", className: TONES.warning },
  conflicting: { label: "충돌", className: TONES.danger },
  reconciled: { label: "대사 완료", className: TONES.success },
  failed: { label: "검증 실패", className: TONES.danger },
} satisfies Record<string, StatusDefinition>;

const ELIGIBILITY = {
  ineligible: { label: "보고서 사용 불가", className: TONES.danger },
  conditional: { label: "조건부 사용", className: TONES.warning },
  eligible: { label: "보고서 사용 가능", className: TONES.success },
} satisfies Record<string, StatusDefinition>;

type BadgeProps<T extends string> = {
  status: T;
  detail?: ReactNode;
  className?: string;
};

function AxisBadge({
  definition,
  axisLabel,
  detail,
  className = "",
}: {
  definition: StatusDefinition;
  axisLabel: string;
  detail?: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`${BASE} ${definition.className} ${className}`}
      aria-label={`${axisLabel}: ${definition.label}`}
      title={typeof detail === "string" ? detail : undefined}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      <span>{definition.label}</span>
      {detail ? <span className="font-medium opacity-80">· {detail}</span> : null}
    </span>
  );
}

export function ExecutionStatusBadge(props: BadgeProps<keyof typeof EXECUTION>) {
  const { status, ...rest } = props;
  return <AxisBadge definition={EXECUTION[status]} axisLabel="실행 상태" {...rest} />;
}

export function ApprovalStatusBadge(props: BadgeProps<keyof typeof APPROVAL>) {
  const { status, ...rest } = props;
  return <AxisBadge definition={APPROVAL[status]} axisLabel="승인 상태" {...rest} />;
}

export function EvidenceStatusBadge(props: BadgeProps<keyof typeof EVIDENCE>) {
  const { status, ...rest } = props;
  return <AxisBadge definition={EVIDENCE[status]} axisLabel="근거 상태" {...rest} />;
}

export function DataQualityBadge(props: BadgeProps<keyof typeof QUALITY>) {
  const { status, ...rest } = props;
  return <AxisBadge definition={QUALITY[status]} axisLabel="데이터 품질" {...rest} />;
}

export function ReportEligibilityBadge(props: BadgeProps<keyof typeof ELIGIBILITY>) {
  const { status, ...rest } = props;
  return <AxisBadge definition={ELIGIBILITY[status]} axisLabel="보고서 적격성" {...rest} />;
}
