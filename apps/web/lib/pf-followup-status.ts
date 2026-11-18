import {
  bindCashflowDependents, CASH_FLOW_DEPENDENT_ENGINES,
  type CalculationRunRecord, type CashflowResult, type CashFlowDependentEngine,
} from "./project-client";

type MissingField = "productMix" | "pfStructure";
type FollowupIssue = {
  status: "needs_input" | "not_supported" | "failed";
  reason?: string;
  missingFields: MissingField[];
};

export type PfFollowupExecution = {
  calculationRunId: string;
  financialInputHash?: string;
  issues: Partial<Record<CashFlowDependentEngine, FollowupIssue>>;
};

export function readPfFollowupExecution(value: unknown): PfFollowupExecution | null {
  if (!value || typeof value !== "object") return null;
  const body = value as Record<string, unknown>;
  if (typeof body.calculationRunId !== "string" || !body.calculationRunId) return null;
  const issues: PfFollowupExecution["issues"] = {};
  if (Array.isArray(body.dependentFailures)) {
    for (const raw of body.dependentFailures) {
      if (!raw || typeof raw !== "object" || !Array.isArray(raw.engines)) continue;
      const { status, reason, missingFields } = raw as Record<string, unknown>;
      if (status !== "needs_input" && status !== "not_supported" && status !== "failed") continue;
      const fields = Array.isArray(missingFields)
        ? missingFields.filter((field): field is MissingField => field === "productMix" || field === "pfStructure")
        : [];
      for (const engine of CASH_FLOW_DEPENDENT_ENGINES) {
        if (raw.engines.includes(engine)) issues[engine] = {
          status, reason: typeof reason === "string" ? reason : undefined, missingFields: fields,
        };
      }
    }
  }
  return {
    calculationRunId: body.calculationRunId,
    financialInputHash: typeof body.financialInputHash === "string" ? body.financialInputHash : undefined,
    issues,
  };
}

export type PfFollowupState = {
  engine: CashFlowDependentEngine;
  status: "completed" | "needs_input" | "unsupported" | "failed" | "unavailable" | "unknown";
  message: string;
  run?: CalculationRunRecord;
  missingFields?: MissingField[];
  scheduleNeeded?: boolean;
};

export function pfFollowupStates(
  cashflow: CashflowResult,
  runs: Record<string, CalculationRunRecord | null> = {},
  execution?: PfFollowupExecution | null,
  unavailableEngines: readonly CashFlowDependentEngine[] = [],
): PfFollowupState[] {
  const bound = bindCashflowDependents(cashflow, runs);
  const input = cashflow.inputSnapshot;
  const issues = execution?.calculationRunId === cashflow.runId
    && (!execution.financialInputHash || execution.financialInputHash === input?.financialInputHash)
    ? execution.issues : {};
  // An explicit empty array in the exact saved input establishes absence.
  // A legacy run without a captured field leaves that requirement unknown.
  const knownMissing = (["productMix", "pfStructure"] as const).filter((field) =>
    Array.isArray(input?.[field]) && input[field].length === 0);

  return CASH_FLOW_DEPENDENT_ENGINES.map((engine) => {
    const run = bound[engine];
    const issue = issues[engine];
    if (run) return { engine, status: "completed", message: "같은 현금흐름과 입력에서 저장된 결과입니다.", run };
    if (cashflow.monthlyFinancing != null || input?.monthlyFinancing != null
      || issue?.reason === "reviewed_monthly_dependent_policy_required") {
      return { engine, status: "unsupported", message: "월별 이자와 상환 방식에 맞는 별도의 후속 계산 규칙이 필요합니다." };
    }
    if (issue?.reason === "cash_flow_stress_period_unconfirmed"
      || (!issue && engine === "discount_stress" && cashflow.periodBasis?.kind === "source_sequence")) {
      return { engine, status: "needs_input", scheduleNeeded: true, message: "원본 사건을 달력 월차와 대응할 사업기간 근거가 필요합니다." };
    }
    if (issue?.status === "needs_input" || (!issue && engine !== "discount_stress" && knownMissing.length)) {
      const missingFields = issue?.missingFields ?? knownMissing;
      const fields = missingFields.map((field) => field === "productMix" ? "상품 구성·분양수입" : "PF 대출 조건");
      return { engine, status: "needs_input", missingFields,
        message: fields.length ? `원본에서 ${fields.join(", ")} 근거를 확인해 주세요.` : "계산에 필요한 입력 근거를 확인해 주세요." };
    }
    if (issue?.status === "not_supported") {
      return { engine, status: "unsupported", message: "이 계산의 입력 방식에 해당하는 후속 계산 규칙이 필요합니다." };
    }
    if (issue?.status === "failed") {
      return { engine, status: "failed", message: "후속 실행에 오류가 발생했습니다. 입력을 검토하고 다시 계산해 주세요." };
    }
    if (unavailableEngines.includes(engine)) {
      return { engine, status: "unavailable", message: "저장된 후속 실행 조회에 실패했습니다. 새로고침해 주세요." };
    }
    return { engine, status: "unknown", message: "이 현금흐름과 연결된 후속 실행 상태를 확인할 수 없습니다." };
  });
}
