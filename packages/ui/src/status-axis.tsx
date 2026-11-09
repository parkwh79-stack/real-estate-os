import { type ReactNode } from "react";

type StatusDefinition = {
  label: string;
  className: string;
};

const BASE =
  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-bold";
const TONES = {
  neutral: "border-re-border bg-re-subtle text-re-secondary",
  info: "border-blue-200 bg-blue-50 text-re-info dark:border-blue-800 dark:bg-blue-950/30",
  success:
    "border-emerald-200 bg-emerald-50 text-re-success dark:border-emerald-800 dark:bg-emerald-950/30",
  warning:
    "border-amber-200 bg-amber-50 text-re-warning dark:border-amber-800 dark:bg-amber-950/30",
  danger:
    "border-red-200 bg-red-50 text-re-danger dark:border-red-800 dark:bg-red-950/30",
} as const;

/** Mirrors parsing_platform.contracts.ExecutionStatus. */
export type ParserExecutionStatus =
  | "pending"
  | "running"
  | "completed"
  | "completed_with_warnings"
  | "failed"
  | "cancelled";

/** Covers the dashboard's initial state and the backend AgentProgress states. */
export type ExecutionStatus = ParserExecutionStatus | "not_started" | "blocked" | "skipped";

/** Mirrors parsing_platform.contracts.ReviewStatus. */
export type ReviewStatus =
  | "not_required"
  | "pending"
  | "in_review"
  | "approved"
  | "rejected"
  | "needs_revision";

/** Mirrors parsing_platform.contracts.ApprovalStatus. */
export type ApprovalStatus =
  | "not_requested"
  | "pending"
  | "approved"
  | "rejected"
  | "superseded";

/** Readiness to request approval; never substitutes for ApprovalStatus. */
export type ApprovalReadinessStatus =
  | "not_started"
  | "evaluating"
  | "not_ready_for_approval"
  | "ready_for_approval"
  | "needs_review"
  | "hold";

/**
 * Evidence states accepted by normalized legal/permit review projections.
 * `candidate` and `located` remain explicit pre-normalization presentation states;
 * neither implies verification.
 */
export type EvidenceStatus =
  | "missing"
  | "weak"
  | "candidate"
  | "located"
  | "partial"
  | "unverified"
  | "verified"
  | "complete"
  | "failed"
  | "unavailable"
  | "superseded";

/** Cross-document/data-validation quality, independent from evidence authority. */
export type DataQualityStatus =
  | "pending"
  | "missing"
  | "missing_data"
  | "partial"
  | "conflicting"
  | "warning"
  | "reconciled"
  | "complete"
  | "blocking"
  | "failed";

/**
 * UI projection of the server's `reportEligible` / `analysisOnly` contract.
 * `ineligible` and `conditional` are retained as compatibility aliases for
 * callers that have not yet migrated to `blocked` and `analysis_only`.
 */
export type ReportEligibilityStatus =
  | "eligible"
  | "analysis_only"
  | "blocked"
  | "ineligible"
  | "conditional";

/**
 * Business meaning of a value. This does not describe source lineage,
 * authority, evidence strength, review, or approval.
 */
export type ValueNature = "reported" | "calculated" | "assumed" | "estimated";

export type UnknownStatus = "unknown";

export type ExecutionStatusPresentation = ExecutionStatus | UnknownStatus;

/**
 * A source profile is part of the mapping contract. Technical runtimes keep
 * their own enums; the UI projects them onto the decision-facing execution
 * axis without rewriting or discarding the raw value.
 */
export type ExecutionStatusProfile = "parser_v2" | "recent_work_v2";

export type ExecutionStatusMappingCode =
  | "EXACT"
  | "QUEUED_AS_PENDING"
  | "RETRYING_AS_RUNNING"
  | "COMPLETED_WITH_BLOCKERS_AS_WARNING"
  | "RECALCULATION_REQUIRED"
  | "NO_EXECUTION_RECORD"
  | "UNKNOWN_SOURCE_STATUS";

export interface ExecutionStatusProjection {
  status: ExecutionStatusPresentation;
  rawStatus: string | null;
  mappingCode: ExecutionStatusMappingCode;
}
export type ReviewStatusPresentation = ReviewStatus | UnknownStatus;
export type ApprovalStatusPresentation = ApprovalStatus | UnknownStatus;
export type ApprovalReadinessPresentation =
  | ApprovalReadinessStatus
  | UnknownStatus;
export type EvidenceStatusPresentation = EvidenceStatus | UnknownStatus;
export type DataQualityStatusPresentation = DataQualityStatus | UnknownStatus;
export type ReportEligibilityPresentation =
  | ReportEligibilityStatus
  | UnknownStatus;
export type ValueNaturePresentation = ValueNature | UnknownStatus;

const EXECUTION = {
  not_started: { label: "미실행", className: TONES.neutral },
  pending: { label: "실행 대기", className: TONES.neutral },
  running: { label: "실행 중", className: TONES.info },
  completed: { label: "실행 완료", className: TONES.success },
  completed_with_warnings: {
    label: "경고와 함께 완료",
    className: TONES.warning,
  },
  failed: { label: "실행 실패", className: TONES.danger },
  cancelled: { label: "실행 취소", className: TONES.neutral },
  blocked: { label: "실행 차단", className: TONES.danger },
  skipped: { label: "실행 건너뜀", className: TONES.neutral },
} satisfies Record<ExecutionStatus, StatusDefinition>;

const PARSER_EXECUTION_STATUSES = new Set<ParserExecutionStatus>([
  "pending",
  "running",
  "completed",
  "completed_with_warnings",
  "failed",
  "cancelled",
]);

const REVIEW = {
  not_required: { label: "검토 불필요", className: TONES.neutral },
  pending: { label: "검토 대기", className: TONES.neutral },
  in_review: { label: "검토 중", className: TONES.info },
  approved: { label: "검토 완료", className: TONES.success },
  rejected: { label: "검토 반려", className: TONES.danger },
  needs_revision: { label: "보완 필요", className: TONES.warning },
} satisfies Record<ReviewStatus, StatusDefinition>;

const APPROVAL = {
  not_requested: { label: "승인 미요청", className: TONES.neutral },
  pending: { label: "승인 대기", className: TONES.info },
  approved: { label: "승인", className: TONES.success },
  rejected: { label: "승인 반려", className: TONES.danger },
  superseded: { label: "새 버전으로 대체", className: TONES.neutral },
} satisfies Record<ApprovalStatus, StatusDefinition>;

const APPROVAL_READINESS = {
  not_started: { label: "승인 준비 미시작", className: TONES.neutral },
  evaluating: { label: "승인 준비 평가 중", className: TONES.info },
  not_ready_for_approval: { label: "승인 준비 미완료", className: TONES.danger },
  ready_for_approval: { label: "승인 요청 가능", className: TONES.success },
  needs_review: { label: "승인 전 검토 필요", className: TONES.warning },
  hold: { label: "승인 보류", className: TONES.warning },
} satisfies Record<ApprovalReadinessStatus, StatusDefinition>;

const EVIDENCE = {
  missing: { label: "근거 없음", className: TONES.danger },
  weak: { label: "근거 취약", className: TONES.warning },
  candidate: { label: "근거 후보", className: TONES.warning },
  located: { label: "근거 위치 확인", className: TONES.info },
  partial: { label: "근거 일부 확인", className: TONES.warning },
  unverified: { label: "근거 미검증", className: TONES.warning },
  verified: { label: "근거 검증", className: TONES.success },
  complete: { label: "근거 완비", className: TONES.success },
  failed: { label: "근거 검증 실패", className: TONES.danger },
  unavailable: { label: "근거 확인 불가", className: TONES.danger },
  superseded: { label: "근거 대체됨", className: TONES.neutral },
} satisfies Record<EvidenceStatus, StatusDefinition>;

const QUALITY = {
  pending: { label: "품질 검증 대기", className: TONES.neutral },
  missing: { label: "데이터 누락", className: TONES.danger },
  missing_data: { label: "필수 데이터 누락", className: TONES.danger },
  partial: { label: "부분 데이터", className: TONES.warning },
  conflicting: { label: "데이터 충돌", className: TONES.danger },
  warning: { label: "품질 주의", className: TONES.warning },
  reconciled: { label: "대사 완료", className: TONES.success },
  complete: { label: "품질 검증 완료", className: TONES.success },
  blocking: { label: "품질 차단", className: TONES.danger },
  failed: { label: "품질 검증 실패", className: TONES.danger },
} satisfies Record<DataQualityStatus, StatusDefinition>;

const ELIGIBILITY = {
  eligible: { label: "보고서 사용 가능", className: TONES.success },
  analysis_only: { label: "분석 전용", className: TONES.warning },
  blocked: { label: "보고서 사용 차단", className: TONES.danger },
  ineligible: { label: "보고서 사용 불가", className: TONES.danger },
  conditional: { label: "조건부 사용", className: TONES.warning },
} satisfies Record<ReportEligibilityStatus, StatusDefinition>;

const VALUE_NATURE = {
  reported: { label: "보고값", className: TONES.info },
  calculated: { label: "계산값", className: TONES.info },
  assumed: { label: "가정값", className: TONES.warning },
  estimated: { label: "추정값", className: TONES.warning },
} satisfies Record<ValueNature, StatusDefinition>;

const UNKNOWN_DEFINITION: StatusDefinition = {
  label: "알 수 없는 상태",
  className: TONES.danger,
};

type BadgeProps<T extends string> = {
  status: T | UnknownStatus;
  detail?: ReactNode;
  className?: string;
};

function hasDefinition<T extends string>(
  definitions: Record<T, StatusDefinition>,
  value: unknown,
): value is T {
  return (
    typeof value === "string" &&
    Object.prototype.hasOwnProperty.call(definitions, value)
  );
}

function normalizeStatus<T extends string>(
  value: unknown,
  definitions: Record<T, StatusDefinition>,
): T | UnknownStatus {
  return hasDefinition(definitions, value) ? value : "unknown";
}

export function normalizeExecutionStatus(
  value: unknown,
): ExecutionStatusPresentation {
  return normalizeStatus(value, EXECUTION);
}

/**
 * Projects a source-specific execution state onto the canonical UI axis.
 * Unknown and absent states fail closed; they are never inferred as completed.
 * Existing normalizeExecutionStatus callers intentionally remain unchanged.
 */
export function projectExecutionStatus(
  profile: ExecutionStatusProfile,
  value: unknown,
): ExecutionStatusProjection {
  const rawStatus =
    typeof value === "string" && value.trim()
      ? value.trim().toLowerCase()
      : null;

  if (rawStatus === null) {
    return {
      status: "unknown",
      rawStatus,
      mappingCode: "NO_EXECUTION_RECORD",
    };
  }

  const exact = normalizeExecutionStatus(rawStatus);
  const exactAllowed =
    exact !== "unknown" &&
    (profile !== "parser_v2" ||
      PARSER_EXECUTION_STATUSES.has(exact as ParserExecutionStatus));
  if (exactAllowed) {
    return { status: exact, rawStatus, mappingCode: "EXACT" };
  }

  if (profile === "recent_work_v2") {
    const aliases: Record<
      string,
      Pick<ExecutionStatusProjection, "status" | "mappingCode">
    > = {
      queued: { status: "pending", mappingCode: "QUEUED_AS_PENDING" },
      retrying: { status: "running", mappingCode: "RETRYING_AS_RUNNING" },
      completed_with_blockers: {
        status: "completed_with_warnings",
        mappingCode: "COMPLETED_WITH_BLOCKERS_AS_WARNING",
      },
      needs_recalculation: {
        status: "completed_with_warnings",
        mappingCode: "RECALCULATION_REQUIRED",
      },
    };
    const projected = aliases[rawStatus];
    if (projected) return { ...projected, rawStatus };
  }

  return {
    status: "unknown",
    rawStatus,
    mappingCode: "UNKNOWN_SOURCE_STATUS",
  };
}

export function normalizeReviewStatus(
  value: unknown,
): ReviewStatusPresentation {
  return normalizeStatus(value, REVIEW);
}

export function normalizeApprovalStatus(
  value: unknown,
): ApprovalStatusPresentation {
  return normalizeStatus(value, APPROVAL);
}

export function normalizeApprovalReadinessStatus(
  value: unknown,
): ApprovalReadinessPresentation {
  return normalizeStatus(value, APPROVAL_READINESS);
}

export function normalizeEvidenceStatus(
  value: unknown,
): EvidenceStatusPresentation {
  return normalizeStatus(value, EVIDENCE);
}

export function normalizeDataQualityStatus(
  value: unknown,
): DataQualityStatusPresentation {
  return normalizeStatus(value, QUALITY);
}

export function normalizeReportEligibility(
  value: unknown,
): ReportEligibilityPresentation {
  return normalizeStatus(value, ELIGIBILITY);
}

export function normalizeValueNature(value: unknown): ValueNaturePresentation {
  return normalizeStatus(value, VALUE_NATURE);
}

function AxisBadge({
  definition,
  axisLabel,
  status,
  unknown,
  detail,
  className = "",
}: {
  definition: StatusDefinition;
  axisLabel: string;
  status: string;
  unknown: boolean;
  detail?: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`${BASE} ${definition.className} ${className}`}
      aria-label={`${axisLabel}: ${definition.label}`}
      title={typeof detail === "string" ? detail : undefined}
      data-status-axis={axisLabel}
      data-status={status}
      data-unknown-status={unknown ? "true" : undefined}
    >
      <span
        aria-hidden="true"
        className="h-1.5 w-1.5 rounded-full bg-current"
      />
      <span>{definition.label}</span>
      {detail ? (
        <span className="font-medium opacity-80">· {detail}</span>
      ) : null}
    </span>
  );
}

function definitionFor<T extends string>(
  definitions: Record<T, StatusDefinition>,
  status: T | UnknownStatus,
): StatusDefinition {
  return hasDefinition(definitions, status)
    ? definitions[status]
    : UNKNOWN_DEFINITION;
}

export function ExecutionStatusBadge(props: BadgeProps<ExecutionStatus>) {
  const { status, ...rest } = props;
  return (
    <AxisBadge
      definition={definitionFor(EXECUTION, status)}
      axisLabel="실행 상태"
      status={status}
      unknown={!hasDefinition(EXECUTION, status)}
      {...rest}
    />
  );
}

export function ReviewStatusBadge(props: BadgeProps<ReviewStatus>) {
  const { status, ...rest } = props;
  return (
    <AxisBadge
      definition={definitionFor(REVIEW, status)}
      axisLabel="검토 상태"
      status={status}
      unknown={!hasDefinition(REVIEW, status)}
      {...rest}
    />
  );
}

export function ApprovalStatusBadge(props: BadgeProps<ApprovalStatus>) {
  const { status, ...rest } = props;
  return (
    <AxisBadge
      definition={definitionFor(APPROVAL, status)}
      axisLabel="승인 상태"
      status={status}
      unknown={!hasDefinition(APPROVAL, status)}
      {...rest}
    />
  );
}

export function ApprovalReadinessBadge(
  props: BadgeProps<ApprovalReadinessStatus>,
) {
  const { status, ...rest } = props;
  return (
    <AxisBadge
      definition={definitionFor(APPROVAL_READINESS, status)}
      axisLabel="승인 준비 상태"
      status={status}
      unknown={!hasDefinition(APPROVAL_READINESS, status)}
      {...rest}
    />
  );
}

export function EvidenceStatusBadge(props: BadgeProps<EvidenceStatus>) {
  const { status, ...rest } = props;
  return (
    <AxisBadge
      definition={definitionFor(EVIDENCE, status)}
      axisLabel="근거 상태"
      status={status}
      unknown={!hasDefinition(EVIDENCE, status)}
      {...rest}
    />
  );
}

export function DataQualityBadge(props: BadgeProps<DataQualityStatus>) {
  const { status, ...rest } = props;
  return (
    <AxisBadge
      definition={definitionFor(QUALITY, status)}
      axisLabel="데이터 품질"
      status={status}
      unknown={!hasDefinition(QUALITY, status)}
      {...rest}
    />
  );
}

export function ReportEligibilityBadge(
  props: BadgeProps<ReportEligibilityStatus>,
) {
  const { status, ...rest } = props;
  return (
    <AxisBadge
      definition={definitionFor(ELIGIBILITY, status)}
      axisLabel="보고서 적격성"
      status={status}
      unknown={!hasDefinition(ELIGIBILITY, status)}
      {...rest}
    />
  );
}

export function ValueNatureBadge(props: BadgeProps<ValueNature>) {
  const { status, ...rest } = props;
  return (
    <AxisBadge
      definition={definitionFor(VALUE_NATURE, status)}
      axisLabel="값의 성격"
      status={status}
      unknown={!hasDefinition(VALUE_NATURE, status)}
      {...rest}
    />
  );
}
