export type TimingPeriod = {
  sourcePeriodIndex: number; label: string; periodKind: "event" | "monthly" | "unknown";
  sourceMonthNumber: number | null;
  sourceLocator?: { sourceName?: string; sheet?: string; cell?: string; monthCell?: string };
};
export type CashFlowTimingSource = {
  sourceBasisHash: string;
  basis: { periods: TimingPeriod[]; warnings?: string[] };
  sourceDocument?: { originalFileName?: string; archiveId?: string };
};
export type CashFlowTiming = {
  schemaVersion: "cash-flow-timing-assumptions-v1"; sourceBasisHash: string;
  baseDate: string | null; firstMonthlyMonthIndex: number | null;
  unlistedMonthsAreZero: boolean | null;
  eventBindings: { sourcePeriodIndex: number; plannedDate: string | null; monthIndex: number | null }[];
  collections: { monthIndex: number | null; contract: number | null; interim: number | null; balance: number | null }[];
};
export type CashFlowTimingDraft = {
  sourceBasisHash: string; baseDate: string; firstMonthlyMonthIndex: string; remove: boolean;
  unlistedMonthsAreZero: boolean;
  eventBindings: Record<number, { plannedDate: string; monthIndex: string }>;
  collections: { monthIndex: string; contract: string; interim: string; balance: string }[];
};

export function timingDraft(saved?: CashFlowTiming | null): CashFlowTimingDraft {
  return {
    sourceBasisHash: saved?.sourceBasisHash ?? "", baseDate: saved?.baseDate ?? "",
    firstMonthlyMonthIndex: saved?.firstMonthlyMonthIndex?.toString() ?? "", remove: false,
    unlistedMonthsAreZero: saved?.unlistedMonthsAreZero === true,
    eventBindings: Object.fromEntries((saved?.eventBindings ?? []).map((row) => [row.sourcePeriodIndex,
      { plannedDate: row.plannedDate ?? "", monthIndex: row.monthIndex?.toString() ?? "" }])),
    collections: (saved?.collections ?? []).map((row) => ({ monthIndex: row.monthIndex?.toString() ?? "",
      contract: row.contract?.toString() ?? "", interim: row.interim?.toString() ?? "", balance: row.balance?.toString() ?? "" })),
  };
}

function integer(value: string, label: string, high = 240): number | null {
  const text = value.trim();
  if (!text) return null;
  if (!/^\d+$/.test(text) || !Number.isSafeInteger(Number(text)) || Number(text) > high) {
    throw new Error(`${label}: 0~${high} 범위의 정수를 입력하세요.`);
  }
  return Number(text);
}

function calendarDate(value: string): string | null {
  const text = value.trim();
  if (!text) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text) || text.startsWith("0000-") || !Number.isFinite(Date.parse(text))
    || new Date(text).toISOString().slice(0, 10) !== text) {
    throw new Error("분석용 날짜는 YYYY-MM-DD 형식의 실제 날짜로 입력하세요.");
  }
  return text;
}

export function timingPayload(draft: CashFlowTimingDraft, source?: CashFlowTimingSource | null): CashFlowTiming | null {
  if (draft.remove) return null;
  if (!source || (draft.sourceBasisHash && source.sourceBasisHash !== draft.sourceBasisHash)) {
    throw new Error("시간 가정의 원본 기간이 바뀌었습니다. 최신 원본에서 시간 가정을 다시 입력하세요.");
  }
  const collections = draft.collections.map((row) => ({ monthIndex: integer(row.monthIndex, "수금 월차"),
    contract: integer(row.contract, "계약금(원)", Number.MAX_SAFE_INTEGER),
    interim: integer(row.interim, "중도금(원)", Number.MAX_SAFE_INTEGER),
    balance: integer(row.balance, "잔금(원)", Number.MAX_SAFE_INTEGER) }));
  const months = collections.map((row) => row.monthIndex).filter((month) => month !== null);
  if (new Set(months).size !== months.length) throw new Error("같은 월차의 수금은 한 행에 합쳐 입력하세요.");
  return {
    schemaVersion: "cash-flow-timing-assumptions-v1", sourceBasisHash: source.sourceBasisHash,
    baseDate: calendarDate(draft.baseDate), firstMonthlyMonthIndex: integer(draft.firstMonthlyMonthIndex, "원본 첫 월의 계산 월차"),
    unlistedMonthsAreZero: draft.unlistedMonthsAreZero,
    eventBindings: source.basis.periods.filter((row) => row.periodKind === "event").map((row) => ({
      sourcePeriodIndex: row.sourcePeriodIndex,
      plannedDate: calendarDate(draft.eventBindings[row.sourcePeriodIndex]?.plannedDate ?? ""),
      monthIndex: integer(draft.eventBindings[row.sourcePeriodIndex]?.monthIndex ?? "", "사건 계산 월차"),
    })), collections,
  };
}
